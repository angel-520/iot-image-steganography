"""V2 authenticated, rate-limited TCP/UDP file transfer, alongside legacy covert channels.

This is encrypted application-data transfer, not a TCP covert-channel algorithm.
PCAP records reconstruct IP headers around observed socket data; they are not NIC captures.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import secrets
import socket
import struct
import time

from . import network_peer as peer
from . import network_stego as net
from .istg_pack import pack_istg, pack_binary
from .istg_crypto import encrypt_istg, decrypt_istg
from .security_analysis.packet import parse_istg

MAX_FILE = 8 * 1024 * 1024
MAX_PACKET = MAX_FILE + 8192
MAX_SECONDS = 300
LENGTH = struct.Struct('!I')


def pack_content(data, secret, cipher='AES-256-GCM', filename=None):
    peer.auth_key(secret)
    if cipher not in peer.CIPHERS:
        raise ValueError('不支持此加密类型')
    if filename is None:
        if not isinstance(data, str) or not 1 <= len(data.encode('utf-8')) <= 65536:
            raise ValueError('分块传输文本需为 1–65536 UTF-8 字节')
        packet = pack_istg('text', data)
    else:
        if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_FILE:
            raise ValueError('网络文件需为 1 字节至 8 MiB')
        packet = pack_binary(data, filename)
    return encrypt_istg(packet, secret, cipher) if cipher != 'none' else packet


def unpack_content(packet, secret, allowed_ciphers, expected_cipher):
    parsed = parse_istg(packet)
    cipher = parsed['metadata'].get('cipher')
    if cipher != expected_cipher or cipher not in allowed_ciphers:
        raise ValueError('接收端未允许此加密类型，或类型不匹配')
    if parsed['encrypted']:
        parsed = parse_istg(decrypt_istg(packet, secret))
    content = parsed['payload_data']
    if content['kind'] not in ('text', 'file') or not 1 <= len(content['data']) <= MAX_FILE:
        raise ValueError('载荷类型或大小无效')
    if content['kind'] == 'text' and len(content['data']) > 65536:
        raise ValueError('接收文字超过 64 KiB')
    return content


class Trace:
    def __init__(self, transport):
        self.transport = transport
        self.origin = time.perf_counter()
        self.rows = []
        self.sequences = {}
        self.size = 0
        self.truncated = False

    def add(self, data, source, destination, stamp=None):
        if len(self.rows) >= 30000 or self.size + len(data) + 64 > 24 * 1024 * 1024:
            self.truncated = True; return
        key = (source, destination)
        if not self.rows and stamp is not None:
            self.origin = min(self.origin, stamp)
        seq = self.sequences.get(key, 1) if self.transport == 'tcp' else None
        row = net.make_record((stamp or time.perf_counter()) - self.origin, data,
            src=source[0], dst=destination[0], sport=source[1], dport=destination[1], tcp_seq=seq)
        self.rows.append(row); self.size += len(data) + 64
        if seq is not None:
            self.sequences[key] = (seq + len(data)) % 2**32


def receive_frame(sock, deadline, stop=None):
    def exact(size):
        result = bytearray()
        while len(result) < size:
            if time.monotonic() >= deadline or (stop and stop.is_set()):
                raise ValueError('TCP 接收超时或已停止')
            try:
                part = sock.recv(size - len(result))
            except socket.timeout:
                continue
            if not part:
                raise ValueError('TCP 连接已关闭，传输未完成')
            result.extend(part)
        return bytes(result)
    size = LENGTH.unpack(exact(4))[0]
    if not peer.WIRE.size + peer.TAG_SIZE <= size <= peer.WIRE.size + peer.MAX_BODY + peer.TAG_SIZE:
        raise ValueError('TCP 帧长度无效')
    return exact(size)


class Engine:
    def __init__(self, secret, output, transport, allowed_ciphers, message_sink):
        self.secret, self.key = secret, peer.auth_key(secret)
        self.output, self.transport = Path(output), transport
        self.output.mkdir(parents=True, exist_ok=True)
        # Load Scapy while starting the listener, before a peer's handshake timeout starts.
        net.make_record(0., b'')
        self.allowed_ciphers = tuple(allowed_ciphers)
        self.message_sink = message_sink
        self.current = self.completed = None

    def manifest(self, body):
        value = peer.read_json(body)
        if set(value) != {'version', 'transport', 'count', 'total', 'chunk_size', 'digest', 'cipher'}:
            raise ValueError('文件会话参数不完整')
        if value['version'] != 2 or value['transport'] != self.transport or value['cipher'] not in peer.CIPHERS:
            raise ValueError('会话协议或加密类型无效')
        if (any(type(value[k]) is not int for k in ('count','total','chunk_size'))
                or not 512 <= value['chunk_size'] <= 1200 or not 1 <= value['total'] <= MAX_PACKET
                or not 1 <= value['count'] <= 16384 or value['count'] != (value['total']+value['chunk_size']-1)//value['chunk_size']):
            raise ValueError('文件会话超过分块或大小限制')
        import re
        if not isinstance(value['digest'],str) or not re.fullmatch('[0-9a-f]{64}',value['digest']):
            raise ValueError('文件摘要无效')
        return value

    def process(self, data, address, local, stamp=None):
        now = time.monotonic()
        if self.current and now-self.current['started'] > MAX_SECONDS:
            self.current = None
        if self.completed and now-self.completed['finished'] > 15:
            self.completed = None
        decoded = peer.decode_wire(data,self.key)
        if decoded and decoded[0] == peer.HELLO:
            _,session,_,body = decoded
            if self.completed and self.completed['session'] == session:
                return None
            if self.current:
                if self.current['session'] == session and self.current['address'] == address and self.current['body'] == body:
                    return peer.encode_wire(peer.READY,session,0,self.current['challenge'],self.key)
                return None
            try:
                manifest = self.manifest(body)
            except (ValueError,TypeError):
                return None
            if manifest['cipher'] not in self.allowed_ciphers:
                return peer.encode_wire(peer.STATUS,session,0,peer.json_bytes({'status':'failed','error':'cipher_not_allowed'}),self.key)
            challenge=secrets.token_bytes(16)
            self.current={'session':session,'address':address,'local':local,'manifest':manifest,'body':body,
                'challenge':challenge,'key':peer.session_key(self.key,session,challenge),
                'rows':{},'started':now,'trace':Trace(self.transport)}
            self.current['trace'].add((LENGTH.pack(len(data))+data) if self.transport=='tcp' else data,address,local,stamp)
            response=peer.encode_wire(peer.READY,session,0,challenge,self.key)
            self.record_response(response,self.current)
            return response
        if self.completed and self.completed['address']==address:
            old=peer.decode_wire(data,self.completed['key'])
            if old and old[0]==peer.FINISH and old[1]==self.completed['session'] and not old[3]:
                return self.completed['response']
        current=self.current
        if not current or current['address']!=address:
            return None
        decoded=peer.decode_wire(data,current['key'])
        if not decoded or decoded[1]!=current['session']:
            return None
        kind,session,index,body=decoded
        manifest=current['manifest']
        current['trace'].add((LENGTH.pack(len(data))+data) if self.transport=='tcp' else data,address,local,stamp)
        response=None
        if kind==peer.DATA:
            expected=min(manifest['chunk_size'],manifest['total']-index*manifest['chunk_size'])
            if index<manifest['count'] and len(body)==expected:
                current['rows'].setdefault(index,body)
        elif kind==peer.FINISH:
            if body:
                try:
                    window=peer.read_json(body)
                    start,end=window['start'],window['end']
                    if (set(window)!={'start','end'} or type(start) is not int or type(end) is not int
                            or not 0<=start<end<=manifest['count'] or end-start>64):
                        return None
                except (ValueError,KeyError,TypeError):
                    return None
                missing=[i for i in range(start,end) if i not in current['rows']]
                response=peer.encode_wire(peer.MISSING if missing else peer.STATUS,session,0,
                    peer.json_bytes({'missing':missing,'end':end} if missing else {'status':'window_ok','end':end}),current['key'])
            else:
                missing=[i for i in range(manifest['count']) if i not in current['rows']]
                if missing:
                    response=peer.encode_wire(peer.MISSING,session,0,peer.json_bytes({'missing':missing[:64]}),current['key'])
                else:
                    receipt=self.commit(current)
                    response=peer.encode_wire(peer.STATUS,session,0,peer.json_bytes(receipt),current['key'])
                    self.completed={**current,'finished':now,'response':response}
                    self.current=None
        if response:
            self.record_response(response,current)
        return response

    def record_response(self,response,current):
        data=LENGTH.pack(len(response))+response if self.transport=='tcp' else response
        current['trace'].add(data,current['local'],current['address'])

    def commit(self,current):
        paths=[]
        try:
            packet=b''.join(current['rows'][i] for i in range(current['manifest']['count']))
            if sha256(packet).hexdigest()!=current['manifest']['digest']:
                raise ValueError('传输包摘要不匹配')
            content=unpack_content(packet,self.secret,self.allowed_ciphers,current['manifest']['cipher'])
            data=content['data']
            filename=content.get('filename','message.txt')
            stem='transfer_'+secrets.token_hex(8)
            payload_path,pcap_path=self.output/(stem+'.payload'),self.output/(stem+'.pcap')
            paths=[payload_path,pcap_path]
            with payload_path.open('xb') as target:
                target.write(data)
            with pcap_path.open('xb') as target:
                target.write(net.pcap_bytes(current['trace'].rows))
            message={'file':str(payload_path),'pcap':str(pcap_path),'peer':current['address'][0],
                'session':current['session'].hex(),'kind':content['kind'],'filename':filename,'size_bytes':len(data),
                'text':data.decode('utf-8') if content['kind']=='text' else None,
                'cipher':current['manifest']['cipher'],'transport':self.transport,
                'source':list(current['address']),'destination':list(current['local']),
                'trace_truncated':current['trace'].truncated}
            if self.message_sink:
                self.message_sink(message)
            return {'status':'ok','packet_sha256':current['manifest']['digest'],'plaintext_sha256':sha256(data).hexdigest(),
                'bytes':len(data),'received_packets':len(current['rows']),'file':filename[:40], 'pcap':pcap_path.name,
                'source':list(current['address']),'destination':list(current['local']),
                'transport':self.transport,'cipher':current['manifest']['cipher'],'trace_truncated':current['trace'].truncated}
        except (ValueError,OSError):
            for path in paths:
                path.unlink(missing_ok=True)
            return {'status':'failed','error':'integrity_or_storage_failed'}


class UDPReceiver(peer.Receiver):
    """Accept both V1 covert messages and V2 file sessions on the selected UDP port."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.engine=Engine(self.secret,self.output,'udp',self.allowed_ciphers,self.message_sink)

    def process(self,data,address):
        if address[0]!=self.allowed_peer:
            return
        decoded=peer.decode_wire(data,self.key)
        version2=False
        if decoded and decoded[0]==peer.HELLO:
            try:
                version2=peer.read_json(decoded[3]).get('version')==2
            except ValueError:
                return
        active=self.engine.current or self.engine.completed
        session=peer.WIRE.unpack_from(data)[2] if len(data)>=peer.WIRE.size else None
        if version2 or (active and session==active['session']):
            if self.current:
                return
            reply=self.engine.process(data,address,peer.local_endpoint(self.socket,address),time.perf_counter())
            if reply:
                self.socket.sendto(reply,address)
        elif not self.engine.current:
            super().process(data,address)

    def progress(self):
        current=self.engine.current or self.current
        return (len(current['rows']),current['manifest']['count']) if current else (0,None)

    def close(self):
        super().close()
        if hasattr(self,'engine'):
            self.engine.secret=''; self.engine.key=b''
            self.engine.current=self.engine.completed=None


class TCPReceiver:
    def __init__(self,bind_host,port,allowed_peer,secret,output_dir,message_sink=None,allowed_ciphers=None):
        if bind_host!='0.0.0.0':
            peer.ipv4(bind_host)
        self.allowed_peer=peer.ipv4(allowed_peer); peer.port_number(port)
        self.secret,self.key=secret,peer.auth_key(secret)
        self.current=self.completed=None
        self.engine=Engine(secret,output_dir,'tcp',allowed_ciphers or peer.CIPHERS[:2],message_sink)
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            self.socket.bind((bind_host,port)); self.socket.listen(4); self.socket.settimeout(.1)
        except Exception:
            self.socket.close(); raise

    def progress(self):
        current=self.engine.current
        return (len(current['rows']),current['manifest']['count']) if current else (0,None)

    def serve(self,stop=None,timeout=300):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline and not (stop and stop.is_set()):
            try:
                connection,address=self.socket.accept()
            except socket.timeout:
                continue
            with connection:
                if address[0]!=self.allowed_peer:
                    continue
                connection.settimeout(.1)
                handshake_deadline=min(deadline,time.monotonic()+5)
                try:
                    while time.monotonic()<deadline and not (stop and stop.is_set()):
                        wire=receive_frame(connection,handshake_deadline if not self.engine.current else min(deadline,self.engine.current['started']+MAX_SECONDS),stop)
                        response=self.engine.process(wire,address,connection.getsockname(),time.perf_counter())
                        if response:
                            connection.sendall(LENGTH.pack(len(response))+response)
                        if self.engine.completed and not self.engine.current:
                            break
                except (ValueError,OSError):
                    pass
                finally:
                    self.engine.current=None

    def close(self):
        self.socket.close(); self.secret=''; self.key=b''
        self.engine.secret=''; self.engine.key=b''; self.engine.current=self.engine.completed=None


def transmit(destination,packet,plaintext_digest,*,transport='tcp',cipher='AES-256-GCM',rate_mbps=5.,
             chunk_size=1024,window_size=32,retries=3,progress=None,trace_sink=None):
    if transport not in ('tcp','udp') or cipher not in peer.CIPHERS:
        raise ValueError('传输协议或加密类型无效')
    if not .05<=rate_mbps<=100 or not 512<=chunk_size<=1200 or not 1<=window_size<=64 or not 0<=retries<=5:
        raise ValueError('速率、分块、窗口或重试参数超限')
    count=(len(packet)+chunk_size-1)//chunk_size
    if not 1<=len(packet)<=MAX_PACKET or count>16384:
        raise ValueError('传输文件或分块数超限')
    if (len(packet)+count*77)*8/(rate_mbps*1_000_000)>240:
        raise ValueError('当前速率下预计超过 240 秒，请提高速率或减小文件')
    session=secrets.token_bytes(8); master=peer.auth_key(destination.secret)
    target=(destination.host,destination.port)
    trace=Trace(transport); started=time.monotonic(); deadline=started+MAX_SECONDS
    sent=retransmitted=0
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM if transport=='tcp' else socket.SOCK_DGRAM)
    with sock:
        sock.settimeout(3)
        try:
            sock.connect(target)
        except OSError as exc:
            raise ValueError('无法连接接收方，请检查协议、IP、端口及防火墙设置') from exc
        source=sock.getsockname(); sock.settimeout(.1)
        next_send=time.perf_counter()
        def send(kind,index,body,key,limited=False):
            nonlocal next_send,sent
            if time.monotonic()>deadline:
                raise ValueError('传输超过 300 秒时限')
            wire=peer.encode_wire(kind,session,index,body,key)
            data=LENGTH.pack(len(wire))+wire if transport=='tcp' else wire
            if limited:
                now=time.perf_counter()
                next_send=max(next_send,now)+len(data)*8/(rate_mbps*1_000_000)
                time.sleep(max(0.,next_send-time.perf_counter()))
            try:
                if transport=='tcp':
                    sock.settimeout(1)
                    sock.sendall(data)
                    sock.settimeout(.1)
                else:
                    sock.send(data)
            except OSError as exc:
                raise ValueError('网络发送失败，接收方可能已停止') from exc
            trace.add(data,source,target)
            if kind==peer.DATA:
                sent+=1
        def receive(key,timeout=2,window_end=None):
            limit=min(deadline,time.monotonic()+timeout)
            while time.monotonic()<limit:
                try:
                    wire=receive_frame(sock,limit) if transport=='tcp' else sock.recv(2048)
                except socket.timeout:
                    continue
                except (ConnectionResetError,ConnectionRefusedError):
                    return None
                decoded=peer.decode_wire(wire,key)
                if decoded and decoded[1]==session and decoded[0] in (peer.READY,peer.STATUS,peer.MISSING):
                    if window_end is not None:
                        value=peer.read_json(decoded[3])
                        if value.get('end')!=window_end:
                            continue
                    trace.add(LENGTH.pack(len(wire))+wire if transport=='tcp' else wire,target,source)
                    return decoded
            return None
        manifest=peer.json_bytes({'version':2,'transport':transport,'count':count,'total':len(packet),
            'chunk_size':chunk_size,'digest':sha256(packet).hexdigest(),'cipher':cipher})
        response=None
        for _ in range(3 if transport=='udp' else 1):
            send(peer.HELLO,0,manifest,master)
            response=receive(master)
            if response:
                break
        if response and response[0]==peer.STATUS:
            raise ValueError('接收方未允许所选加密类型，请在接收设置中启用相应类型')
        if not response or response[0]!=peer.READY or len(response[3])!=16:
            raise ValueError('认证握手失败，请检查相同共享口令、允许来源 IP 及 TCP/UDP 选择')
        key=peer.session_key(master,session,response[3])
        if transport=='tcp':
            for index in range(count):
                send(peer.DATA,index,packet[index*chunk_size:(index+1)*chunk_size],key,True)
                if progress and index%32==0:
                    progress(15+int(index/count*70),f'TCP 已发送 {index+1}/{count} 块')
        else:
            for start in range(0,count,window_size):
                end=min(count,start+window_size); pending=list(range(start,end)); acknowledged=False
                for attempt in range(retries+1):
                    for index in pending:
                        send(peer.DATA,index,packet[index*chunk_size:(index+1)*chunk_size],key,True)
                        if attempt:
                            retransmitted+=1
                    send(peer.FINISH,0,peer.json_bytes({'start':start,'end':end}),key)
                    response=receive(key,window_end=end)
                    if response and response[0]==peer.STATUS:
                        acknowledged=True; break
                    if response:
                        missing=peer.read_json(response[3]).get('missing')
                        if not isinstance(missing,list) or any(type(i)is not int or not start<=i<end for i in missing):
                            raise ValueError('接收端重传请求无效')
                        pending=sorted(set(missing))
                if not acknowledged:
                    raise ValueError('UDP 窗口超过重试次数，未确认交付')
                if progress:
                    progress(15+int(end/count*70),f'UDP 已确认 {end}/{count} 块')
        receipt=None
        for _ in range(3 if transport=='udp' else 1):
            send(peer.FINISH,0,b'',key)
            response=receive(key,timeout=5)
            if response:
                receipt=peer.read_json(response[3]); break
        if (not receipt or receipt.get('status')!='ok' or receipt.get('packet_sha256')!=sha256(packet).hexdigest()
                or receipt.get('plaintext_sha256')!=plaintext_digest):
            raise ValueError('未收到通过校验和保存的交付回执，请检查接收端')
        if trace_sink:
            trace_sink(trace.rows)
        elapsed=time.monotonic()-started
        return {'status':'ok','peer':destination.public(),'transport':transport,'channel':'data','cipher':cipher,
            'receipt':receipt,'source':list(source),'destination':list(target),'sent_datagrams':sent,
            'retransmitted_datagrams':retransmitted,'elapsed_seconds':elapsed,'rate_mbps':rate_mbps,
            'effective_mbps':receipt['bytes']*8/max(elapsed,.001)/1_000_000,
            'trace_truncated':trace.truncated,'wire_overhead_bytes':peer.WIRE.size+peer.TAG_SIZE}
