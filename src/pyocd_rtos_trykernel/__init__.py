# TryKernel pyOCD RTOS Plugin
# Copyright (C) 2023 Kenta Ida
# SPDX-License-Identifier: Apache-2.0
# This plugin is based on pyOCD FreeRTOS plugin.
# The original license is as follows:
# pyOCD debugger
# Copyright (c) 2016-2020 Arm Limited
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pyocd.rtos.provider import (TargetThread, ThreadProvider)
from pyocd.rtos.common import (read_c_string, HandlerModeThread, EXC_RETURN_EXT_FRAME_MASK)
from pyocd.core import exceptions
from pyocd.core.target import Target
from pyocd.core.plugin import Plugin
from pyocd.debug.context import DebugContext
from pyocd.coresight.cortex_m_core_registers import index_for_reg
import logging

THREAD_CONTEXT_OFFSET = 0
THREAD_PRIORITY_OFFSET = 20
THREAD_STATE_OFFSET = 12

# Create a logger for this module.
LOG = logging.getLogger(__name__)

class TryKernelThreadContext(DebugContext):
    """@brief Thread context for Try Kernel."""

    # SP/PSP are handled specially, so it is not in these dicts.

    REGISTER_OFFSETS = {
                # registers below are saved by dispatcher
                 8: 0, # r8
                 9: 4, # r9
                 10: 8, # r10
                 11: 12, # r11
                 4: 16, # r4
                 5: 20, # r5
                 6: 24, # r6
                 7: 28, # r7
                 # registers below are saved by processor core when exception entry
                 0: 32, # r0
                 1: 36, # r1
                 2: 40, # r2
                 3: 44, # r3
                 12: 48, # r12
                 14: 52, # lr
                 15: 56, # pc
                 16: 60, # xpsr
            }

    def __init__(self, parent, thread):
        super(TryKernelThreadContext, self).__init__(parent)
        self._thread = thread
        self._has_fpu = self.core.has_fpu

    def read_core_registers_raw(self, reg_list):
        reg_list = [index_for_reg(reg) for reg in reg_list]
        reg_vals = []

        isCurrent = self._thread.is_current
        inException = isCurrent and self._parent.read_core_register('ipsr') > 0

        # If this is the current thread and we're not in an exception, just read the live registers.
        if isCurrent and not inException:
            return self._parent.read_core_registers_raw(reg_list)

        # Because of above tests, from now on, inException implies isCurrent;
        # we are generating the thread view for the RTOS thread where the
        # exception occurred; the actual Handler Mode thread view is produced
        # by HandlerModeThread
        if inException:
            # Reasonable to assume PSP is still valid
            sp = self._parent.read_core_register('psp')
        else:
            sp = self._thread.get_stack_pointer()
        
        table = self.REGISTER_OFFSETS
        
        for reg in reg_list:
            # Must handle stack pointer specially.
            if reg == 13:
                if inException:
                    reg_vals.append(sp + 64)
                else:
                    reg_vals.append(sp + 64 + 4)  # the saved sp is after saving cotext.
                continue

            # Look up offset for this register on the stack.
            spOffset = table.get(reg, None)
            if spOffset is None:
                reg_vals.append(self._parent.read_core_register_raw(reg))
                continue

            try:
                if spOffset >= 0:
                    reg_vals.append(self._parent.read32(sp + spOffset))
                else:
                    # Not available - try live one
                    reg_vals.append(self._parent.read_core_register_raw(reg))
            except exceptions.TransferError:
                reg_vals.append(0)

        return reg_vals

class TryKernelThread(TargetThread):
    """@brief A TryKernel task."""

    NONEXIST = 0
    READY    = 1
    WAIT     = 2
    DORMANT  = 8
    RUNNING  = 128

    STATE_NAMES = {
            NONEXIST: "NONEXIST",
            READY   : "READY",
            WAIT    : "WAIT",
            DORMANT : "DORMANT",
            RUNNING : "RUNNING",
        }

    def __init__(self, targetContext, provider, base, index):
        super(TryKernelThread, self).__init__()
        self._target_context = targetContext
        self._provider = provider
        self._base = base
        self._state = self._target_context.read32(self._base + THREAD_STATE_OFFSET)
        self._thread_context = TryKernelThreadContext(self._target_context, self)

        self._priority = self._target_context.read32(self._base + THREAD_PRIORITY_OFFSET)

        self._index = index
        self._name = f"task{self._index}"

    def get_stack_pointer(self):
        # Get stack pointer saved in thread struct.
        try:
            return self._target_context.read32(self._base + THREAD_CONTEXT_OFFSET)
        except exceptions.TransferError:
            LOG.debug("Transfer error while reading thread's stack pointer @ 0x%08x", self._base + THREAD_CONTEXT_OFFSET)
            return 0

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value

    @property
    def priority(self):
        return self._priority

    @property
    def unique_id(self):
        return self._base

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return "%s; Priority %d" % (self.STATE_NAMES[self.state], self.priority)

    @property
    def is_current(self):
        return self._provider.get_actual_current_thread_id() == self.unique_id

    @property
    def context(self):
        return self._thread_context

    def __str__(self):
        return "<TryKernelThread@0x%08x id=%x name=%s>" % (id(self), self.unique_id, self.name)

    def __repr__(self):
        return str(self)

class TryKernelThreadProvider(ThreadProvider):
    """@brief Thread provider for TryKernel."""

    ## Required TryKernel symbols.
    TRYKERNEL_SYMBOLS = [
        "tcb_tbl",
        "cur_task",
        ]

    def __init__(self, target):
        super(TryKernelThreadProvider, self).__init__(target)
        self._symbols = None
        self._total_priorities = 0
        self._threads = {}

    def init(self, symbolProvider):
        # Lookup required symbols.
        self._symbols = self._lookup_symbols(self.TRYKERNEL_SYMBOLS, symbolProvider)
        if self._symbols is None:
            LOG.warn("TryKernel: failed to find TryKernel symbols")
            return False

        self._target.session.subscribe(self.event_handler, Target.Event.POST_FLASH_PROGRAM)
        self._target.session.subscribe(self.event_handler, Target.Event.POST_RESET)

        LOG.info("TryKernel: initialized. symbols=%s", self._symbols)

        return True

    def invalidate(self):
        self._threads = {}

    def event_handler(self, notification):
        # Invalidate threads list if flash is reprogrammed.
        LOG.debug("TryKernel: invalidating threads list: %s" % (repr(notification)))
        self.invalidate()

    def _build_thread_list(self):
        LOG.debug("TryKernel: building thread list")
        newThreads = {}

        maxThreadCount = 32 # CNF_MAX_TSKID
        threadTable = self._symbols['tcb_tbl']

        # Read the current thread.
        currentThread = self._target_context.read32(self._symbols['cur_task'])

        for threadIndex in range(maxThreadCount):
            threadBase = threadTable + threadIndex * 64
            try:
                t = TryKernelThread(self._target_context, self, threadBase, threadIndex)

                # Set thread state.
                LOG.debug("TryKernel: thread 0x%08x state: %d", threadBase, t.state)
                if threadBase == currentThread:
                    t.state = TryKernelThread.RUNNING

                if t.state == TryKernelThread.NONEXIST:
                    continue
                LOG.debug("Thread 0x%08x (%s)", threadBase, t.name)
                newThreads[t.unique_id] = t
            except exceptions.TransferError:
                LOG.debug("TransferError while examining thread 0x%08x", threadBase)

        # Create fake handler mode thread.
        if self._target_context.read_core_register('ipsr') > 0:
            LOG.debug("TryKernel: creating handler mode thread")
            t = HandlerModeThread(self._target_context, self)
            newThreads[t.unique_id] = t

        self._threads = newThreads

    def get_threads(self):
        if not self.is_enabled:
            return []
        self.update_threads()
        return list(self._threads.values())

    def get_thread(self, threadId):
        if not self.is_enabled:
            return None
        self.update_threads()
        return self._threads.get(threadId, None)

    @property
    def is_enabled(self):
        return self._symbols is not None and self.get_is_running()

    @property
    def current_thread(self):
        if not self.is_enabled:
            return None
        self.update_threads()
        id = self.get_current_thread_id()
        try:
            return self._threads[id]
        except KeyError:
            return None

    def is_valid_thread_id(self, threadId):
        if not self.is_enabled:
            return False
        self.update_threads()
        return threadId in self._threads

    def get_current_thread_id(self):
        if not self.is_enabled:
            return None
        if self._target_context.read_core_register('ipsr') > 0:
            return HandlerModeThread.UNIQUE_ID
        return self.get_actual_current_thread_id()

    def get_actual_current_thread_id(self):
        if not self.is_enabled:
            return None
        return self._target_context.read32(self._symbols['cur_task'])

    def get_is_running(self):
        if self._symbols is None:
            return False
        try:
            return self._target_context.read32(self._symbols['tcb_tbl']) != 0
        except exceptions.TransferFaultError:
            LOG.warn("TryKernel: read running state failed, target memory might not be initialized yet.")
            return False

class TryKernelPlugin(Plugin):
    """@brief Plugin class for TryKernel."""

    def load(self):
        return TryKernelThreadProvider

    @property
    def name(self):
        return "trykernel"

    @property
    def description(self):
        return "TryKernel"