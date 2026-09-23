"""Windows kill-on-close scope for temporary diagnostic process trees."""
import ctypes
import os
from ctypes import wintypes
import psutil


class ProcessScope:
    def __init__(self):
        self.handle = None
        self.children = []
        if os.name != "nt":
            return
        class Basic(ctypes.Structure):
            _fields_ = [("per_process", ctypes.c_int64), ("per_job", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if self.handle and not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.handle:
            return
        # A Windows venv redirector can spawn the real Python before Popen
        # returns. Capture existing descendants too; new children now inherit
        # the job. The diagnostic runner waits for our ownership gate.
        for child in psutil.Process(process.pid).children(recursive=True):
            handle = self.api.OpenProcess(0x0100 | 0x0001 | 0x0400, False, child.pid)
            if not handle:
                if not child.is_running():
                    continue
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                present = wintypes.BOOL()
                if not self.api.IsProcessInJob(handle, self.handle, ctypes.byref(present)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not present.value:
                    # Venv redirectors may own a separate nested job already.
                    # Windows cannot merge its tree into a populated sibling
                    # job, but allows a fresh child job for this process.
                    child_scope = ProcessScope()
                    self.children.append(child_scope)
                    if not self.api.AssignProcessToJobObject(child_scope.handle, handle):
                        if child.is_running():
                            raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.api.CloseHandle(handle)

    def close(self):
        for child in self.children:
            child.close()
        self.children.clear()
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
