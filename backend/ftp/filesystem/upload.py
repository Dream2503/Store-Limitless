from threading import Lock


class FTPUploadFile:
    def __init__(self, filesystem, file, path, mode):
        self.filesystem = filesystem
        self.file = file
        self.path = path
        self.mode = mode
        self.name = str(path)
        self.closed = False
        self._lock = Lock()
        self.file_object = path.open(mode)

    def read(self, size=-1):
        return self.file_object.read(size)

    def readinto(self, buffer):
        return self.file_object.readinto(buffer)

    def write(self, data):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return self.file_object.write(data)

    def seek(self, offset, whence=0):
        return self.file_object.seek(offset, whence)

    def tell(self):
        return self.file_object.tell()

    def flush(self):
        return self.file_object.flush()

    def fileno(self):
        return self.file_object.fileno()

    def readable(self):
        return self.file_object.readable()

    def writable(self):
        return self.file_object.writable()

    def seekable(self):
        return self.file_object.seekable()

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True

            try:
                self.file_object.flush()
            finally:
                self.file_object.close()

        self.filesystem._finish_upload(self.file, self.path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
