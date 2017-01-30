from socketserver import TCPServer, BaseRequestHandler, ThreadingTCPServer
import socket
from pathlib import Path, PurePosixPath
from enum import Enum

class LocalFileSystem():
    def __init__(self, basedir):
        self._basedir = Path(basedir)

    def absolute(self, path: PurePosixPath):
        path = '/' / path
        path = (self._basedir / path.as_posix()[1:]) #type: Path
        path.relative_to(self._basedir)
        return path
    
    def iterdir(self, path):
        path = self.absolute(path)
        for entry in path.iterdir(): #type: Path
            yield entry.name + ('/' if entry.is_dir() else '')

    def is_dir(self, path):
        path = self.absolute(path)
        return path.is_dir()

    def exists(self, path):
        path = self.absolute(path)
        return path.exists()
    
    def mkdir(self, path):
        path = self.absolute(path)
        path.mkdir()

    def rmd(self, path):
        path = self.absolute(path)
        path.rmdir()

    def write_bytes(self, path, iterator):
        path = self.absolute(path)
        with path.open(mode='wb') as f:
            for data in iterator:
                f.write(data)
    
    def read_bytes(self, path):
        path = self.absolute(path)
        with path.open(mode='rb') as f:
            while True:
                data = f.read()
                if not data:
                    break
                yield data
                
    

class CmdRequestHandlerFactory():
    "Generate CmdRequestHandlers serving specified filesystem"
    def __init__(self, filesystem):
        self._fs = filesystem
    
    def __call__(self, request, client_address, server):
        return CmdRequestHandler(request, client_address, server, self._fs)
        
class CmdRequestHandler(BaseRequestHandler):
    "Handle a FTP session on Command port"

    def __init__(self, request, client_address, server, filesystem):
        self._fs = filesystem
        self._cwd = PurePosixPath('/')
        BaseRequestHandler.__init__(self, request, client_address, server)

    def handle(self):
        self.request.settimeout(30)
        try:
            self.reply(220) #Service ready for new user.
            while True:
                cmd = self.request.recv(8192).decode().strip().split(maxsplit=1) #type: List[str]
                if not cmd:
                    break
                print(cmd)
                try:
                    handler = self.__getattribute__('handle_'+cmd[0].upper())
                except AttributeError:
                    self.handle_unknown(*cmd)
                else:
                    handler(*cmd[1:])
                    # else:
                    #     print(handler.__code__.co_argcount)
                    #     self.reply(501)
                         
        except socket.timeout: #pylint: disable=E1101
            pass
        finally:
            print('closed\r\n')

    def handle_unknown(self, *args):
        if args[0] in ('ACCT', 'ALLO', 'SITE'):
            self.reply(202) #Command not implemented, superfluous at this site.
        else:
            self.reply(502) #Command not implemented.
    
    def handle_USER(self, user=None): #Minimum implementation
        # self.reply(331) #User name okay, need password.
        self.reply(230) #User logged in, proceed.

    # def handle_PASS(self, pwd=None):
    #     self.reply(230) #User logged in, proceed.

    def handle_PWD(self):
        self.reply(257, '"%s"' % self._cwd.as_posix()) #"PATHNAME" created.
    
    def handle_CWD(self, path):
        path = self._cwd / path

        if self._fs.is_dir(path):
            self._cwd = path
            self.reply(250) #Requested file action okay, completed.
        else:
            self.reply(550) #Requested action not taken. File unavailable (e.g., file not found, no access).
    
    def handle_MKD(self, path):
        path = self._cwd / path

        try:
            self._fs.mkdir(path)
            self.reply(257, '"%s"' % path.as_posix()) #"PATHNAME" created.
        except (ValueError, FileExistsError):
            self.reply(550)
    
    def handle_RMD(self, path):
        path = self._cwd / path

        self.reply(250) #Requested file action okay, completed.

    # def handle_SYST(self):
    #     self.reply(215, "UNIX ")
    
    # def handle_FEAT(self):
    #     self.reply(211) #no-features 

    def handle_TYPE(self, typecode): #Minimum implementation
        self.reply(200) #200 Command okay.

    # def handle_MODE(self, mode): #Minimum implementation
    #     pass
    # def handle_QUIT(self): #Minimum implementation
    #     pass
    def handle_PORT(self, port): #Minimum implementation
        try:
            h1, h2, h3, h4, p1, p2 = map(int, port.split(','))
            data_host = ('.'.join(map(str, (h1,h2,h3,h4))), p1*256+p2)
        except ValueError:
            self.reply(501)
        else:
            if data_host[0] != self.request.getpeername()[0]:
                self.reply(501)
            else:
                self._data_host = data_host
                self.reply(200) #200 Command okay.

    # def handle_STRU(self, structure): #Minimum implementation
    #     pass


    def recv_data(self):
        self.reply(150) #File status okay; about to open data connection.
        print(self._data_host)
        with socket.create_connection(self._data_host) as conn:
            while True:
                data = conn.recv(8192)
                if not data:
                    break
                yield data
        self.reply(226) #Closing data connection. Requested file action successful

    def send_data(self, iterator):
        self.reply(150) #File status okay; about to open data connection.
        with socket.create_connection(self._data_host) as conn:
            for data in iterator:
                print(data)
                conn.sendall(data)
        self.reply(226) #Closing data connection. Requested file action successful

    def handle_STOR(self, path): #Minimum implementation
        path = self._cwd / path
        self._fs.write_bytes(path, self.recv_data())

    def handle_RETR(self, path): #Minimum implementation
        path = self._cwd / path
        self.send_data(self._fs.read_bytes(path))

    # def handle_NOOP(self): #Minimum implementation
    #     pass

    def handle_SIZE(self, path):
        path = self._cwd / path
        self.reply(312, '0')

    def handle_LIST(self, path='.'):
        path = self._cwd / path

        if not self._fs.is_dir(path):
            self.reply(550)
        else:
            self.send_data(((entry+'\r\n').encode() for entry in self._fs.iterdir(path)))

        

    def reply(self, code, *args):
        print(code, args)
        self.request.send(("%d %s\r\n" % (code, " ".join(args))).encode())
    

class FTPServer():
    def __init__(self, server_address, filesystem):
        self._cmdserver = TCPServer(server_address, CmdRequestHandlerFactory(filesystem))
    def serve_forever(self):
        self._cmdserver.serve_forever()
    def shutdown(self):
        self._cmdserver.shutdown()

from ftplib import FTP
from threading import Thread
import time
import tempfile
from io import BytesIO

class MiniFTPTest():

    
    def test_basic(self):
        # serve_forever()
        with tempfile.TemporaryDirectory() as temp:            
            server = FTPServer(('127.0.0.1',8021), filesystem=LocalFileSystem('./.temp'))
            server_thread = Thread(target=server.serve_forever)
            server_thread.start()
            try:
                # ftp = FTP()
                # ftp.connect(host='127.0.0.1',port=8021)
                # ftp.set_pasv(False)
                # ftp.cwd('/')
                # ftp.retrlines('LIST',print)
                # # ftp.mkd('Test')
                # ftp.pwd()
                # ftp.storlines('STOR text.txt', BytesIO(b"text"))
                # ftp.retrlines('RETR text.txt', print)
                # ftp.close()

                time.sleep(30)
            except EOFError:
                pass
            finally:
                server.shutdown()
        
MiniFTPTest().test_basic()
# FTPServer().serve_forever()

