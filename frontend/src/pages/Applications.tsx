import { useEffect, useRef, useState } from 'react';
import { ArrowDownOutlined, ArrowRightOutlined } from '@ant-design/icons';
import '../styles/applications.css';

const imageRoot = '/images/applications';

interface ScenePhotoProps {
  src: string;
  alt: string;
  className?: string;
  eager?: boolean;
  height?: number;
}

function ScenePhoto({ src, alt, className = '', eager = false, height = 1600 }: ScenePhotoProps) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  return <div className={`application-photo ${className}`} data-loaded={loaded}>
    <img src={src} alt={alt} width={2400} height={height}
      loading={eager ? 'eager' : 'lazy'} fetchPriority={eager ? 'high' : undefined} decoding="async"
      onLoad={() => setLoaded(true)} onError={() => setFailed(true)} hidden={failed} />
    {failed && <span className="application-photo__fallback">图片暂时无法显示</span>}
  </div>;
}

function useSceneReveal() {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = root.current;
    if (!element || !('IntersectionObserver' in window)) return;
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const targets = Array.from(element.querySelectorAll<HTMLElement>('[data-reveal]'));
    const scenes = Array.from(element.querySelectorAll<HTMLElement>('[data-scene]'));
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-revealed');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    const imageObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-in-view');
          imageObserver.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08 });
    const updateMotion = () => {
      element.dataset.motion = preference.matches ? 'reduced' : 'enabled';
      if (preference.matches) targets.forEach(target => target.classList.add('is-revealed'));
    };
    targets.forEach(target => observer.observe(target));
    scenes.forEach(scene => imageObserver.observe(scene));
    updateMotion();
    preference.addEventListener('change', updateMotion);
    return () => {
      observer.disconnect();
      imageObserver.disconnect();
      preference.removeEventListener('change', updateMotion);
      delete element.dataset.motion;
    };
  }, []);
  return root;
}

export default function Applications() {
  const root = useSceneReveal();
  useEffect(() => {
    if (!window.location.hash) window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);
  return <div className="applications-page" ref={root}>
    <section className="applications-hero" aria-labelledby="applications-heading">
      <div className="applications-hero__image" aria-hidden="true">
        <ScenePhoto src={`${imageRoot}/robust-transmission.webp`} alt="" eager />
      </div>
      <div className="applications-hero__content">
        <div className="applications-eyebrow"><span /> ISTG / APPLICATIONS</div>
        <h1 id="applications-heading">让信息隐藏技术<br /><span>走向实际应用</span></h1>
        <p>从影像隐私，到可靠传输与作品溯源。<br />探索一张图像，能够承载的更多价值。</p>
        <a className="applications-scroll-link" href="#medical-imaging"><span>向下探索</span><ArrowDownOutlined /></a>
      </div>
      <div className="applications-hero__signature" aria-hidden="true">Beyond<br /><em>the visible.</em></div>
      <nav className="applications-chapters" aria-label="应用场景章节">
        <a href="#medical-imaging"><span>01</span>医疗影像<ArrowRightOutlined /></a>
        <a href="#robust-transmission"><span>02</span>鲁棒性传输<ArrowRightOutlined /></a>
        <a href="#copyright-protection"><span>03</span>数字版权<ArrowRightOutlined /></a>
      </nav>
    </section>

    <section id="medical-imaging" className="app-scene app-scene--medical" data-scene="medical"
      data-testid="applications-scene" aria-labelledby="medical-heading">
      <div className="medical-scene__image">
        <ScenePhoto src={`${imageRoot}/medical-imaging.webp`} alt="头部 CT 断层扫描影像拼版" />
      </div>
      <div className="medical-scene__shade" aria-hidden="true" />
      <div className="app-scene__index"><span>01</span><span>MEDICAL IMAGING</span></div>
      <div className="medical-scene__content" data-reveal>
        <p className="app-scene__overline">隐私，藏于影像之间。</p>
        <h2 id="medical-heading" aria-label="医疗影像隐私保护">医疗影像<br />隐私保护</h2>
        <h3>让敏感信息得到更隐蔽的保护</h3>
        <p className="app-scene__description">探索将患者信息、检查编号及诊断备注等数据加密后嵌入医学影像，在尽量保持图像视觉质量的同时，实现敏感信息的安全携带与恢复。</p>
      </div>
      <div className="medical-scene__detail" data-reveal aria-hidden="true"><i /><span>ENCRYPTED<br /><b>IN THE IMAGE.</b></span></div>
      <div className="app-scene__footer"><span>影像信息 · 加密携带 · 按需恢复</span><span>ISTG / 01</span></div>
    </section>

    <section id="robust-transmission" className="app-scene app-scene--transmission" data-scene="transmission"
      data-testid="applications-scene" aria-labelledby="transmission-heading">
      <div className="app-scene__index"><span>02</span><span>ROBUST TRANSMISSION</span></div>
      <div className="transmission-scene__heading" data-reveal>
        <div><p className="app-scene__overline">穿越变化，寻找可靠。</p><h2 id="transmission-heading" aria-label="压缩后的鲁棒性传输">压缩后的<br className="applications-mobile-break" />鲁棒性传输</h2></div>
        <h3>探索复杂传输环境下的<br />信息可靠恢复</h3>
      </div>
      <figure className="transmission-scene__comparison" data-reveal>
        <div className="transmission-scene__original"><ScenePhoto src={`${imageRoot}/robust-transmission.webp`} alt="山峰与湖面倒影的原始高清风景照片" /></div>
        <div className="transmission-scene__processed"><ScenePhoto src={`${imageRoot}/robust-transmission-compressed.webp`} alt="同一风景图像经过压缩处理后的视觉示意" /></div>
        <div className="transmission-scene__divider" aria-hidden="true"><span /></div>
        <figcaption><span>原始高清图像</span><span>压缩后图像 · 视觉示意</span></figcaption>
      </figure>
      <div className="transmission-scene__body" data-reveal>
        <p>图像在传输和分享中，可能经历 JPEG 压缩、尺寸调整等处理。研究不同隐写算法的抗干扰能力，探索图像质量变化后，隐藏信息仍能可靠恢复的方法。</p>
      </div>
    </section>

    <section id="copyright-protection" className="app-scene app-scene--copyright" data-scene="copyright"
      data-testid="applications-scene" aria-labelledby="copyright-heading">
      <div className="app-scene__index"><span>03</span><span>DIGITAL OWNERSHIP</span></div>
      <div className="copyright-scene__layout">
        <figure className="copyright-scene__artwork" data-reveal>
          <div className="copyright-scene__frame"><ScenePhoto src={`${imageRoot}/copyright-protection.webp`} alt="Alfred East 的风景油画，呈现芦苇湖面与阳光下的山丘" height={1388} /></div>
          <figcaption><span>ART / IDENTITY / ORIGIN</span><span aria-hidden="true">©</span></figcaption>
        </figure>
        <div className="copyright-scene__content" data-reveal>
          <p className="app-scene__overline">每一份创作，都有来处。</p>
          <h2 id="copyright-heading" aria-label="数字版权保护">数字版权<br />保护</h2>
          <h3>让数字作品拥有<br />可验证的身份标识</h3>
          <p className="app-scene__description">探索信息隐藏与数字水印技术在版权标识中的应用，将版权声明、作者标识等信息融入数字图像，为来源识别与内容溯源提供支持。</p>
        </div>
      </div>
      <div className="copyright-scene__closing"><span>Keep the story.<br /><em>Know the origin.</em></span><span>ISTG STUDIO</span></div>
    </section>
  </div>;
}
