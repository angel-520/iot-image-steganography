import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
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
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
    else window.scrollTo({ top: 0 });
  }, [hash]);
  return <div className="applications-page" ref={root}>
    <section className="applications-hero" aria-labelledby="applications-heading">
      <div className="applications-hero__image" aria-hidden="true"><ScenePhoto src={`${imageRoot}/robust-transmission.webp`} alt="" eager /></div>
      <div className="applications-hero__content">
        <h1 id="applications-heading">多模态<br /><span>信息隐藏</span></h1>
        <div className="applications-hero__actions"><Link className="scene-button scene-button--primary" to="/embed">开始隐写<ArrowRightOutlined aria-hidden="true" /></Link><a className="scene-button" href="#medical-imaging">探索应用<ArrowDownOutlined aria-hidden="true" /></a></div>
      </div>
      <nav className="applications-chapters" aria-label="应用场景章节">
        <a href="#medical-imaging"><span>01</span>影像信息嵌入<ArrowRightOutlined aria-hidden="true" /></a>
        <a href="#robust-transmission"><span>02</span>可逆隐写<ArrowRightOutlined aria-hidden="true" /></a>
        <a href="#copyright-protection"><span>03</span>数字版权<ArrowRightOutlined aria-hidden="true" /></a>
      </nav>
    </section>
    <section id="medical-imaging" className="app-scene app-scene--medical" data-scene="medical" data-testid="applications-scene" aria-labelledby="medical-heading">
      <div className="medical-scene__image"><ScenePhoto src={`${imageRoot}/medical-imaging.webp`} alt="头部 CT 断层扫描影像拼版" /></div>
      <div className="medical-scene__shade" aria-hidden="true" />
      <div className="app-scene__index" aria-hidden="true">01</div>
      <div className="medical-scene__content" data-reveal>
        <h2 id="medical-heading">影像信息<br />嵌入</h2>
        <p className="app-scene__description">将文字、编号和说明嵌入图像，按需提取并核验。</p>
        <Link className="scene-button scene-button--light" to="/applications/medical">进入场景<ArrowRightOutlined aria-hidden="true" /></Link>
      </div>
    </section>
    <section id="robust-transmission" className="app-scene app-scene--transmission" data-scene="transmission" data-testid="applications-scene" aria-labelledby="transmission-heading">
      <div className="app-scene__index" aria-hidden="true">02</div>
      <div className="transmission-scene__heading" data-reveal><h2 id="transmission-heading">可逆图像隐写</h2><Link className="scene-button" to="/applications/robustness">进入场景<ArrowRightOutlined aria-hidden="true" /></Link></div>
      <figure className="transmission-scene__comparison" data-reveal>
        <div className="transmission-scene__original"><ScenePhoto src={`${imageRoot}/robust-transmission.webp`} alt="山峰与湖面倒影的原始风景照片" /></div>
        <div className="transmission-scene__processed"><ScenePhoto src={`${imageRoot}/robust-transmission.webp`} alt="可逆隐写图像的视觉示意" /></div>
        <div className="transmission-scene__divider" aria-hidden="true"><span /></div>
        <figcaption><span>原始图像</span><span>RDH 隐写图 · 视觉示意</span></figcaption>
      </figure>
      <p className="transmission-scene__description" data-reveal>将秘密信息可逆地嵌入像素，提取秘密信息的同时精确恢复原始 RGB 图像。</p>
    </section>
    <section id="copyright-protection" className="app-scene app-scene--copyright" data-scene="copyright" data-testid="applications-scene" aria-labelledby="copyright-heading">
      <div className="app-scene__index" aria-hidden="true">03</div>
      <div className="copyright-scene__layout">
        <figure className="copyright-scene__artwork" data-reveal><div className="copyright-scene__frame"><ScenePhoto src={`${imageRoot}/copyright-protection.webp`} alt="Alfred East 的风景油画，呈现芦苇湖面与阳光下的山丘" height={1388} /></div></figure>
        <div className="copyright-scene__content" data-reveal>
          <h2 id="copyright-heading">数字版权<br />保护</h2>
          <p className="app-scene__description">管理作品、签发版权标识，核验来源并设置访问授权。</p>
          <Link className="scene-button scene-button--light" to="/applications/copyright">进入场景<ArrowRightOutlined aria-hidden="true" /></Link>
        </div>
      </div>
    </section>
    <section className="applications-outro" aria-labelledby="applications-outro-heading">
      <div><h2 id="applications-outro-heading">探索你的图像隐写场景</h2><p>从一个具体任务开始，选择适合的信息隐藏方式。</p></div>
      <div className="applications-outro__actions"><a className="scene-button scene-button--light" href="#medical-imaging">选择应用场景<ArrowRightOutlined aria-hidden="true" /></a><Link className="scene-button scene-button--primary" to="/embed">开始隐写<ArrowRightOutlined aria-hidden="true" /></Link></div>
    </section>
  </div>;
}
