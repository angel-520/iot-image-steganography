import type { Model, KaldiRecognizer } from 'vosk-browser';

/** Offline WASM recognizer: microphone samples never leave this browser. */
export class LocalSpeech {
  private model?: Model; private recognizer?: KaldiRecognizer; private context?: AudioContext;
  private processor?: ScriptProcessorNode; private input?: MediaStreamAudioSourceNode; private silent?: GainNode;
  private active = false;
  private modelUrl = ''; private cancelLoad?: () => void;
  private releaseModel() {
    if (this.model) {
      // Upstream's terminate message calls model.delete even after failed loads.
      // Terminating its owned Worker also releases WASM memory in that case.
      const worker = Reflect.get(this.model, 'worker');
      if (worker instanceof Worker) worker.terminate(); else if (this.model.ready) this.model.terminate();
    }
    this.model = undefined; this.modelUrl = '';
  }
  async start(stream: MediaStream, url: string, result: (text: string) => void, partial: (text: string) => void) {
    if (!stream.getAudioTracks().some(track => track.enabled)) throw Error('请先开启麦克风');
    this.active = true;
    try {
      const response = await fetch(url, { method: 'HEAD' });
      if (!response.ok || response.headers.get('content-type')?.includes('text/html')) throw Error('语音识别资源不可用，请联系管理员。');
      const { Model } = await import('vosk-browser');
      if (!this.model?.ready || this.modelUrl !== url) {
        this.releaseModel(); this.modelUrl = url;
        const model = new Model(new URL(url, window.location.href).href, -1); this.model = model;
        await new Promise<void>((resolve, reject) => {
          const timeout = window.setTimeout(() => reject(Error('语音识别加载超时，请刷新后重试')), 60000);
          this.cancelLoad = () => { window.clearTimeout(timeout); this.cancelLoad = undefined; reject(Error('语音转写已停止')); };
          model.on('load', message => { window.clearTimeout(timeout); this.cancelLoad = undefined; if ('result' in message && message.result) resolve(); else reject(Error('语音识别加载失败')); });
          model.on('error', () => { window.clearTimeout(timeout); this.cancelLoad = undefined; reject(Error('语音识别加载失败，请刷新后重试')); });
        });
      }
      if (!this.active) return;
      this.context = new AudioContext({ sampleRate: 16000 }); await this.context.resume();
      this.recognizer = new this.model.KaldiRecognizer(this.context.sampleRate);
      this.recognizer.on('result', message => { if (this.active && 'result' in message && 'text' in message.result && message.result.text.trim()) result(message.result.text.trim()); });
      this.recognizer.on('partialresult', message => { if (this.active && 'result' in message && 'partial' in message.result) partial(String(message.result.partial)); });
      this.input = this.context.createMediaStreamSource(stream);
      // ScriptProcessor is retained for Edge/Windows compatibility; ASR runs in a WASM Worker.
      this.processor = this.context.createScriptProcessor(4096, 1, 1);
      this.processor.onaudioprocess = event => { if (this.active) this.recognizer?.acceptWaveform(event.inputBuffer); };
      this.silent = this.context.createGain(); this.silent.gain.value = 0;
      this.input.connect(this.processor); this.processor.connect(this.silent); this.silent.connect(this.context.destination);
    } catch (error) { this.close(); throw error; }
  }
  stop() {
    this.cancelLoad?.(); this.active = false; this.input?.disconnect(); this.processor?.disconnect(); this.silent?.disconnect();
    if (this.processor) this.processor.onaudioprocess = null;
    this.recognizer?.remove(); this.recognizer = undefined;
    if (this.context) void this.context.close(); this.context = undefined;
  }
  close() { this.cancelLoad?.(); this.stop(); this.releaseModel(); }
}
