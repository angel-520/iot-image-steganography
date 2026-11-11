"""Local-only browser verification fixture; prints no shared key."""
from pathlib import Path
import json
import os
import secrets
import socket
import sys
import threading

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from istg.network_peer import Receiver
import uvicorn

with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
    probe.bind(('127.0.0.1',0))
    port=probe.getsockname()[1]
os.environ['ISTG_PEER_KEY']=secrets.token_urlsafe(32)
config=root/'.qa'/'browser-peers.json'
config.write_text(json.dumps({'peers':[{'name':'qa-classmate','host':'127.0.0.1','port':port,'key_env':'ISTG_PEER_KEY'}]}),encoding='utf-8')
os.environ['ISTG_NETWORK_PEERS']=str(config)
listener=Receiver('127.0.0.1',port,'127.0.0.1',os.environ['ISTG_PEER_KEY'],root/'.qa'/'peer-inbox')
stop=threading.Event()
thread=threading.Thread(target=listener.serve,kwargs={'stop':stop,'timeout':300},daemon=True)
thread.start()
try:
    uvicorn.run('backend.main:app',host='127.0.0.1',port=8017,access_log=False)
finally:
    stop.set();thread.join(timeout=2);listener.close()
