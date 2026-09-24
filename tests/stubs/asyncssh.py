class Error(Exception): pass
class KeyImportError(ValueError): pass
class SSHClientConnection: pass
class SSHClientProcess: pass
class SSHKey: pass
class SFTPClient: pass
def import_private_key(*a, **k): raise KeyImportError("stub")
async def connect(**k): raise Error("stub")
