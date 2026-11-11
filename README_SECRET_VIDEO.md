# SecretVideo 多人实时音视频隐写

独立入口：`/secret-video` 或顶部导航“通信实验 → 视频通话”。原图像、多模态及网络实验室页面保持兼容，沿用现有 React / Ant Design / ECharts 和配色。

## 实际数据路径

```text
摄像头 ─→ Canvas 640×480 / 15fps ─→ 亮度 DC-QIM 嵌入 ─→ WebRTC VP8 视频
                       ↑                               ↓
聊天 / 文件 / Vosk 转写 → ISTG v1 CBOR → scrypt + AES     接收视频帧 → 纠错 / CRC
                                                        ↓
普通麦克风 ─────────────────────────→ WebRTC 音频       重组 / AES认证 / SHA-256 → 显示与下载
                                                        ↓
发送端“已送达” ←─ 验证 HMAC 回执 ←─ 对端发送视频内的回执帧
```

WebSocket 仅允许入房、成员变化、音视频 SDP / ICE 信令、设备状态及心跳；后端拒绝聊天 / 文件字段和 DataChannel SDP。密钥、文件、聊天正文和转写正文都在浏览器内处理，不发给 HTTP / WebSocket 接口，也不建立 DataChannel。文件下载是接收端验证后的本地 Blob URL。

Canvas 自检先验证不经有损编码的逐字节恢复及 Hamming 单比特纠错。真正通话使用 8×8 亮度块的 DC-QIM，不依赖 WebRTC 无法保留的 RGB 最低位。336 字节帧包含 28 字节头、308 字节数据；每字节使用两个 Hamming(7,4) 码字，空间交织，另有 96 个已知导频位。传输帧保持约 250ms，允许多个视频采样机会。接收位图及认证回执也只能随视频发送。

## 安装与本机运行

Python 3.11+（已使用本机 `cosight_gaia` 的 Python 3.12 验证），Node.js 20.19+：

```powershell
conda activate cosight_gaia
python -m pip install -r requirements.txt
cd frontend
npm ci
cd ..
python tools/prepare_secret_video_model.py
# 可选英文模型：python tools/prepare_secret_video_model.py --language en
cd frontend
npm run build
cd ..
python run.py
```

浏览器打开 `http://127.0.0.1:8000/secret-video`。本机 localhost / 127.0.0.1 可使用摄像头及 WebCrypto。前端开发代理已支持 WebSocket。已有语音模型会跳过下载；本工作区已准备中文和英文模型。模型转换工具从 Vosk 官方下载小模型，重新封装为浏览器所需的 `model/` tar.gz 结构，放到 `frontend/public/models/`，构建时复制进 dist。

新增前端依赖为 `cbor-x`、`@noble/hashes`、`vosk-browser`；测试使用 `playwright`。浏览器封装遵循现有 ISTG v1 和 AES-256-GCM / scrypt 参数，可与 Python 模块双向互解。语音识别使用本地 WebAssembly Worker，不需要 Python ASR 服务、云端识别或 GPU。首次加载 WASM / 语音模型会消耗时间和内存，仅在开启转写时加载；正常通话不需要加载模型。

## 群聊、私聊、文件与转写

1. 输入昵称和群聊隐写密钥，点击“创建房间并加入”。通过其他可信方式将群聊密钥告诉参与者；邀请链接不包含密钥。
2. 复制邀请链接。其他成员打开同一网站的邀请链接，填写群聊密钥并加入，最多 4 人。视频宫格显示每个对端的 WebRTC 连接状态。
3. 至少一个对端显示 `connected` 后发送文字。接收者从视频解码成功后显示正文，发送方仅在收到所有目标成员的认证回执后显示“已送达”。单纯写入 Canvas、收到分块或 CRC 通过都不算最终送达。
4. 文件最高 **128 KiB**，分块随连续视频帧传输，实时显示进度。完整重组、AES 认证和原文件 SHA-256 都通过后提供下载；不会通过普通文件上传接口发送秘密文件。
5. 私聊先选择接收者，再填写独立私聊密钥。对端也需选择发送者并填写双方一致的私聊密钥。密钥按房间和双方成员 ID 分域；其他成员不会解密 / 显示该私聊。私聊密钥应与群聊密钥不同。错误密钥明确显示认证失败，改正后可在持续重传中恢复。
6. 开启“实时通话隐写”，选择已安装模型的语言。浏览器从现有麦克风流本地识别，在自然停顿后把最终转写片段加密写入视频，发给当前选择的接收者。本地尚未定稿的识别结果只显示在发送者页面。关闭开关会断开 ASR 处理节点，**不会停止普通通话音轨**。麦克风静音时停止 ASR。
7. 关闭摄像头时改用生成的封面视频承载数据；关闭麦克风不影响文字与文件隐写。摄像头或麦克风不可用时明确提示实际状态。播放被阻止时点击“播放通话音频”。离开房间会关闭媒体、识别器和连接，并清理本页密钥、消息与下载链接，请先保存文件。

## Windows 两台或多台电脑

假设网站主机的实验 LAN 地址为 **192.168.1.20**，请替换为自己的实际地址。浏览器限制决定远程摄像头需要受信任 HTTPS；通过普通 `http://192.168...` 访问会在页面如实提示，不会伪装设备已开启。

在网站主机创建实验 CA 与证书（30 天有效，不覆盖既有证书）：

```powershell
python tools/secret_video_certificate.py --host 192.168.1.20
python run.py --host 0.0.0.0 --public-host 192.168.1.20 --port 8443 --ssl-certfile .secret-video-tls/server.crt --ssl-keyfile .secret-video-tls/server.key
```

仅把 `.secret-video-tls/ca.crt` 复制给参与实验的电脑，核对它来自自己的网站主机后，在各电脑的 Windows 证书管理器中导入到当前用户的“受信任的根证书颁发机构”，重新打开 Edge / Chrome。**不分享 `ca.key` 或 `server.key`**。证书目录已加入 `.gitignore`。脚本不自动修改信任、系统防火墙或路由配置。

参与者访问 `https://192.168.1.20:8443/secret-video`。在主机 Windows 防火墙中按实验网络需要允许该 Python HTTPS 端口；浏览器的 WebRTC 音视频还需要允许相应 UDP 连通。所有成员应通过证书覆盖的同一实际地址访问；`0.0.0.0` 仅是监听地址。远程监听会开放整个 ISTG 网站，应只用于受控实验网络。

## 跨公网 / NAT

局域网默认仅使用本机网络候选地址，不默认联系第三方 STUN。公网需有参与者都可访问的 HTTPS 网站地址，并根据 NAT 情况配置自己的 STUN / TURN；仅把网站端口映射出去不能保证 WebRTC 音视频可达。可以使用已有合法 HTTPS 证书和支持 WebSocket 的反向代理，或给参与实验的电脑安装实验 CA。

启动前设置由自己管理的 ICE 服务（示例地址及凭证必须替换）：

```powershell
$env:ISTG_RTC_ICE_SERVERS = '[{"urls":"stun:YOUR_ICE_HOST:3478"},{"urls":["turn:YOUR_ICE_HOST:3478?transport=udp","turn:YOUR_ICE_HOST:3478?transport=tcp"],"username":"YOUR_USER","credential":"YOUR_TURN_CREDENTIAL"}]'
python run.py --host 0.0.0.0 --public-host YOUR_HTTPS_HOST --port 8443 --ssl-certfile YOUR_CERT.pem --ssl-keyfile YOUR_KEY.pem
```

ICE 配置会提供给入房浏览器，TURN 凭证应限时 / 限实验使用；不要使用其他服务的长期账户密码。未配置可用 TURN 时，部分 NAT / 防火墙组合将连接失败，页面会显示失败，不会改用普通聊天传输。房间会在最后成员离开 10 分钟后被回收；后端重启后需要重新创建房间。

## 指标、性能与范围

- PSNR 比较本地原始 / 嵌入帧；导频 BER 只比较 96 个已知视频导频位，**不假装能知道未知正文的 BER**。CRC 帧统计包含重复采样帧；Hamming 修正数是码字纠错尝试次数。
- 实测视频码率来自 WebRTC `getStats()`，按所有对端汇总。可设置每个对端 1 / 2.5 / 4 Mbps 的上限。它是浏览器编码上限，不是秘密数据吞吐量保证。
- 理想单向秘密负载上限约 `308 × 4 = 1232 B/s`，还要扣除接收位图、认证回执、丢帧及重传；多条并发任务进一步共享此速率。实时文件限 128 KiB，最多 4 个待发送任务，每任务 15 分钟超时，接收缓存 20 分钟。大文件请使用已有多模态离线模块。
- 文件与文字可能改变块亮度、产生可见画质损失；QIM 16 / 24 / 32 是容量不变的失真 / 抗压缩取舍。当前实现不宣称不可检测，也不保证任何网络 / 编解码器下均能恢复。浏览器降分辨率、强压缩、后台标签页限速和掉线都可能导致失败。全屏遮挡和后台运行时请关注进度。
- 支持 2–4 人 mesh，每个浏览器最多 3 条对等连接。共享群聊密钥是成员间的加密权限约定，不是完善的用户身份认证或企业会议系统。

## 自动化验收

```powershell
python -m unittest discover -s tests -v
node .qa/secret_video_codec_test.mjs
# 另一终端先启动：python run.py --port 8019
node .qa/secret_video_smoke.mjs
node .qa/secret_video_speech_smoke.mjs
# 中文语音验收：
$env:SV_SPEECH_LANGUAGE = 'cn'
node .qa/secret_video_speech_smoke.mjs
```

Node 加密互操作测试默认使用当前 PATH 的 Python，可用 `ISTG_TEST_PYTHON` 指定解释器。浏览器验收使用已安装的 Windows Edge，不自动下载浏览器。

检查内容包括：四个独立浏览器进程以合成动态 Y4M 摄像头进行真实 WebRTC 视频连接，核对音频 RTP 收包及视频实际解码；文字 / 文件经 VP8 编解码后恢复；所有目标成员认证回执；错误私聊密钥拒绝、改正后恢复、非接收者不能显示私聊；模拟 30% 隐写帧丢弃；WebSocket 抓取审计没有正文 / 文件名 / 密钥；任何 DataChannel 创建都令测试失败；手机布局；离房清理密钥和画面。`.qa/secret-video-camera.y4m` 是 FFmpeg testsrc2 生成的合成动态视频夹具。

语音测试使用预生成的 Windows SAPI 合成中文 / 英文 WAV 作为浏览器测试麦克风输入，经实际离线 Vosk 识别，再从另一浏览器收到的视频中恢复转写，检查关闭转写后通话仍连接。`.qa/secret-video-mic.wav` 和 `.qa/secret-video-zh-mic.wav` 是人工合成测试夹具，不是用户录音；可通过 `.qa/create_secret_video_audio.ps1` 重建（需要 Windows 的 Zira / Huihui 语音）。已实际识别并经视频恢复英文 “hello this is a secret video communications test” 和中文“你好 这 是 一个 秘密 视频 通信 测试”。自动化使用同机隔离浏览器；真正多台电脑、真实摄像头 / 麦克风及公网 NAT 仍需按上方说明现场验收，不能把同机测试称为物理双机公网实测。

## 代码与来源

`backend/secret_video.py` 只管理房间与严格信令；`frontend/src/services/secretVideoCodec.ts` 实现视频帧算法；`secretVideoCrypto.ts` 对应原 ISTG 封装 / AES 参数；`secretVideoTransport.ts` 实现视频内 ARQ / 回执；`secretVideoSpeech.ts` 实现本地识别；`frontend/src/pages/SecretVideo.tsx` 为新增页面。

参考：[Canvas captureStream 与 WebRTC](https://developer.chrome.com/blog/capture-stream/)、[摄像头安全上下文](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)、[WebRTC 编码参数](https://developer.mozilla.org/en-US/docs/Web/API/RTCRtpSender/setParameters)、[Vosk Browser](https://github.com/ccoreilly/vosk-browser)、[Vosk 官方模型与许可](https://alphacephei.com/vosk/models)。Vosk WASM / 小模型的首次加载较大，已采用独立异步模块。当前 vosk-browser 上游包包含旧 uuid 依赖；它在识别器中使用 v4 随机标识，不调用 npm audit 提示的 v3/v5/v6 可选输出缓冲区路径。
