# TryKernel RTOS plugin for pyOCD

## 概要

インターフェース 2023年7月号のOS特集で取り上げられているμITRONベースのRTOS「Try Kernel」向けのpyOCD RTOSプラグインです。

pyOCD + GDBでのデバッグ時に、Try KernelのタスクをGDB上のスレッドとして認識し、実行中ではないタスクの退避されたコンテキストの確認や、タスクの次の命令まで実行すると言った操作を行えるようになります。

## インストール方法

本リポジトリをcloneし、以下のコマンドを実行してプラグインをインストールします。

```
git clone https://github.com/ciniml/pyocd-rtos-trykernel
cd pyocd-rtos-trykernel
python3 -m pip install .
```

## 使用方法

Raspberry Pi Picoを2枚用意し、片方にpicoprobeファームウェアやrustdapといったCMSIS-DAPファームウェアを書き込みます。
また、もう1枚のRaspberry Pi PicoのSWD端子と接続しておきます。

以下のコマンドでpyOCDを起動します。

```
pyocd gdbserver --target RP2040 -O rtos.name=trykernel
```

別のコンソールにてTry KernelをビルドしGDBを起動します。

```
git clone https://github.com/ytoyoyama/trykernel -b build_cmake
mkdir -p trykernel/build; cd $_
cmake ..
make -j`nproc`
gdb-multiarch try-kernel -ex "target extended-remote localhost:3333" -ex "monitor reset halt"
```

GDB上では `info threads` コマンドを使用して、タスク一覧を取得できます。その後 `thread` コマンドにてタスクを切り替えて `bt` コマンドによりバックトレースの表示、 `frame` コマンドにより呼び出しフレームの切り替え、 `list` コマンドにより実行中のソースコード周辺の表示を行っています。

```
$ gdb-multiarch ../build/try-kernel -ex "target extended-remote localhost:3333" -ex "monitor reset halt"
GNU gdb (Ubuntu 12.1-0ubuntu1~22.04) 12.1
Copyright (C) 2022 Free Software Foundation, Inc.
License GPLv3+: GNU GPL version 3 or later <http://gnu.org/licenses/gpl.html>
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.
Type "show copying" and "show warranty" for details.
This GDB was configured as "x86_64-linux-gnu".
Type "show configuration" for configuration details.
For bug reporting instructions, please see:
<https://www.gnu.org/software/gdb/bugs/>.
Find the GDB manual and other documentation resources online at:
    <http://www.gnu.org/software/gdb/documentation/>.

For help, type "help".
Type "apropos word" to search for commands related to "word"...
Reading symbols from ../build/try-kernel...
Remote debugging using localhost:3333
disp_020 () at /home/kenta/try_kernel/trykernel/kernel/dispatch.S:45
45          mov         r3, #1
Resetting target with halt
Successfully halted device on reset
(gdb) cont
Continuing.
^C[New Thread 2]
[New Thread 536875212]
[New Thread 536875276]
[New Thread 536875340]
[New Thread 536875404]

Thread 2 "Handler mode" received signal SIGINT, Interrupt.
[Switching to Thread 2]
disp_020 () at /home/kenta/try_kernel/trykernel/kernel/dispatch.S:45
45          mov         r3, #1
(gdb) info threads
  Id   Target Id                                    Frame 
* 2    Thread 2 "Handler mode" (PendSV)             disp_020 () at /home/kenta/try_kernel/trykernel/kernel/dispatch.S:45
  3    Thread 536875212 "task0" (WAIT; Priority 1)  tk_slp_tsk (tmout=tmout@entry=-1) at /home/kenta/try_kernel/trykernel/kernel/task_sync.c:54
  4    Thread 536875276 "task1" (WAIT; Priority 10) tk_dly_tsk (dlytim=dlytim@entry=200) at /home/kenta/try_kernel/trykernel/kernel/task_sync.c:29
  5    Thread 536875340 "task2" (WAIT; Priority 10) tk_dly_tsk (dlytim=dlytim@entry=500) at /home/kenta/try_kernel/trykernel/kernel/task_sync.c:29
  6    Thread 536875404 "task3" (WAIT; Priority 10) tk_dly_tsk (dlytim=<optimized out>) at /home/kenta/try_kernel/trykernel/kernel/task_sync.c:29
(gdb) thread 3
[Switching to thread 3 (Thread 536875212)]
#0  tk_slp_tsk (tmout=tmout@entry=-1) at /home/kenta/try_kernel/trykernel/kernel/task_sync.c:54
54          return err;
(gdb) bt
#0  tk_slp_tsk (tmout=tmout@entry=-1) at /home/kenta/try_kernel/trykernel/kernel/task_sync.c:54
#1  0x10000570 in usermain () at /home/kenta/try_kernel/trykernel/application/usermain.c:47
#2  0x10000f60 in initsk (stacd=<optimized out>, exinf=<optimized out>) at /home/kenta/try_kernel/trykernel/kernel/inittsk.c:25
#3  0x00000000 in ?? ()
(gdb) frame 1
#1  0x10000570 in usermain () at /home/kenta/try_kernel/trykernel/application/usermain.c:47
47          tk_slp_tsk(TMO_FEVR);       // 初期タスクを待ち状態に
(gdb) list
42
43          /* LCD制御タスクの生成・実行 */
44          tskid_lcd = tk_cre_tsk(&ctsk_lcd);
45          tk_sta_tsk(tskid_lcd, 0);
46
47          tk_slp_tsk(TMO_FEVR);       // 初期タスクを待ち状態に
48          return 0;
49      }
(gdb) 
```

## ライセンス

本プラグインは、pyOCDに付属のFreeRTOS向けプラグイン (freertos.py) をもとに作成しています。
pyOCD自体がApache Licenseに従うため、本プラグインも同様に Apache Licenseに従うものとします。
