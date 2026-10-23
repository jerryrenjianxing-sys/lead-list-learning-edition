"""Windows process ownership survives additional kernel references."""
import os
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows job object lifetime')
def test_close_ends_owned_tree_while_another_job_handle_remains(tmp_path):
    import ctypes
    from ctypes import wintypes
    import psutil
    from workbench.process_scope import ProcessScope
    from workbench.queue import kill_tree

    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.GetCurrentProcess.restype=wintypes.HANDLE
    api.DuplicateHandle.argtypes=[wintypes.HANDLE,wintypes.HANDLE,wintypes.HANDLE,
                                 ctypes.POINTER(wintypes.HANDLE),wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    api.CloseHandle.argtypes=[wintypes.HANDLE]
    gate=tmp_path/'start-child'
    child_pid=tmp_path/'child-pid'
    script="""
import pathlib,subprocess,sys,time
gate=pathlib.Path(sys.argv[1])
while not gate.exists():time.sleep(.01)
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
pathlib.Path(sys.argv[2]).write_text(str(child.pid))
time.sleep(60)
"""
    # Use the real interpreter so the test controls when descendants are born.
    outside=subprocess.Popen([sys._base_executable,'-c','import time;time.sleep(60)'],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    parent=subprocess.Popen([sys._base_executable,'-c',script,str(gate),str(child_pid)],
                            creationflags=subprocess.CREATE_NO_WINDOW)
    parent_created=psutil.Process(parent.pid).create_time()
    outside_created=psutil.Process(outside.pid).create_time()
    scope=ProcessScope()
    duplicate=wintypes.HANDLE()
    descendants=[]
    try:
        scope.assign(parent)
        current=api.GetCurrentProcess()
        # Retain a genuine extra kernel reference, rather than simulating exit.
        assert api.DuplicateHandle(current,scope.handle,current,ctypes.byref(duplicate),0,False,2)
        gate.touch()
        deadline=time.monotonic()+5
        while not child_pid.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert child_pid.exists()
        descendants=psutil.Process(parent.pid).children(recursive=True)
        assert any(p.pid==int(child_pid.read_text()) for p in descendants)
        scope.close()
        parent.wait(timeout=1)
        assert not psutil.wait_procs(descendants,timeout=1)[1]
        assert outside.poll() is None
        scope.close()  # Repeated owner cleanup is harmless.
    finally:
        if duplicate.value:api.CloseHandle(duplicate)
        scope.close()
        kill_tree(parent.pid,parent_created)
        kill_tree(outside.pid,outside_created)
