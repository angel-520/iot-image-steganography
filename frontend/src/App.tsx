import { lazy, Suspense, useEffect, useState } from 'react';
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { Button, Drawer, Dropdown, Skeleton } from 'antd';
import { DownOutlined, MenuOutlined } from '@ant-design/icons';
import { ExperimentProvider } from './hooks/useExperiments';
import { api } from './services/api';
import Embed from './pages/Embed';
import Extract from './pages/Extract';
const Analysis = lazy(() => import('./pages/Analysis'));
const Compare = lazy(() => import('./pages/Compare'));
const Applications = lazy(() => import('./pages/Applications'));
const Multimodal = lazy(() => import('./pages/Multimodal'));
const NetworkLab = lazy(() => import('./pages/NetworkLab'));
const SecretVideo = lazy(() => import('./pages/SecretVideo'));

const navigation = [
  { label: '隐写实验', items: [{ path: '/embed', label: '信息隐藏' }, { path: '/extract', label: '信息提取' }, { path: '/multimodal', label: '多模态实验' }] },
  { label: '分析评估', items: [{ path: '/analysis', label: '安全分析' }, { path: '/compare', label: '算法对比' }] },
  { label: '通信实验', items: [{ path: '/network-lab', label: '网络隐写' }, { path: '/secret-video', label: '视频通话' }] },
];

function LegacyApplications() {
  const { search, hash } = useLocation();
  return <Navigate to={`/${search}${hash}`} replace />;
}

function StudioLayout() {
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [openGroup, setOpenGroup] = useState<string>();
  const [health, setHealth] = useState<'loading' | 'ok' | 'offline'>('loading');
  const home = location.pathname === '/';
  const returnHome = () => { setDrawerOpen(false); if (home) window.scrollTo({ top: 0 }); };
  useEffect(() => {
    let active = true;
    const check = () => { void api.get('/health').then(() => { if (active) setHealth('ok'); }).catch(() => { if (active) setHealth('offline'); }); };
    check(); const id = window.setInterval(check, 30_000);
    return () => { active = false; window.clearInterval(id); };
  }, []);
  useEffect(() => {
    setDrawerOpen(false); setOpenGroup(undefined);
    if (!location.hash) window.scrollTo({ top: 0 });
  }, [location.pathname, location.hash]);
  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 1024px)');
    const close = () => { setDrawerOpen(false); setOpenGroup(undefined); };
    desktop.addEventListener('change', close);
    return () => desktop.removeEventListener('change', close);
  }, []);
  return <div className="studio-shell">
    <a className="skip-link" href="#main-content">跳至主要内容</a>
    <header className="site-header"><div className="site-header-inner">
      <Link to="/" className="brand" aria-label="ISTG Studio 首页" onClick={returnHome}><span className="brand-mark" aria-hidden="true"><span/><span/><span/><span/></span><span className="brand-name">ISTG <span>Studio</span></span></Link>
      <nav className="desktop-navigation" aria-label="主导航">
        <NavLink to="/" end className="top-nav-link" onClick={returnHome}>首页</NavLink>
        {navigation.map(group => <Dropdown key={group.label} autoFocus trigger={['hover', 'click']} open={openGroup === group.label} onOpenChange={open => setOpenGroup(current => open ? group.label : current === group.label ? undefined : current)}
          menu={{ selectedKeys: [location.pathname], items: group.items.map(item => ({ key: item.path, label: <Link to={item.path} onClick={() => setOpenGroup(undefined)}>{item.label}</Link> })), onClick: () => setOpenGroup(undefined) }}>
          <button type="button" aria-label={group.label} className={`top-nav-link${group.items.some(item => item.path === location.pathname) ? ' active' : ''}`} aria-haspopup="menu" aria-expanded={openGroup === group.label}
            onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpenGroup(group.label); } if (event.key === 'Escape') setOpenGroup(undefined); }}>{group.label}<DownOutlined/></button>
        </Dropdown>)}
      </nav>
      <div className="site-header-actions">
        {!home && <div className="header-status" role="status"><span className={`connection-dot ${health}`}/><span>{health === 'ok' ? '已连接' : health === 'offline' ? '连接中断' : '正在连接'}</span></div>}
        <Link to="/embed" className="header-start">开始实验</Link>
        <Button type="text" className="mobile-menu-button" aria-label="打开导航" aria-expanded={drawerOpen} icon={<MenuOutlined/>} onClick={() => setDrawerOpen(true)}/>
      </div>
    </div></header>
    <Drawer title="导航" placement="right" width="min(360px, 100vw)" open={drawerOpen} onClose={() => setDrawerOpen(false)}>
      <nav className="mobile-navigation" aria-label="移动导航"><NavLink to="/" end onClick={returnHome}>首页</NavLink>
        {navigation.map(group => <section key={group.label}><h2>{group.label}</h2>{group.items.map(item => <NavLink key={item.path} to={item.path} onClick={() => setDrawerOpen(false)}>{item.label}</NavLink>)}</section>)}
      </nav>
    </Drawer>
    <div className="studio-main"><main id="main-content" className={`page-content${home ? ' page-content--immersive' : ''}`} tabIndex={-1}><div className={`route-view${home ? ' route-view--immersive' : ''}`} key={location.pathname}>
      <Suspense fallback={<Skeleton active paragraph={{rows:10}}/>}><Routes>
        <Route path="/" element={<Applications/>}/><Route path="/embed" element={<Embed/>}/><Route path="/extract" element={<Extract/>}/><Route path="/multimodal" element={<Multimodal/>}/><Route path="/network-lab" element={<NetworkLab/>}/><Route path="/secret-video" element={<SecretVideo/>}/><Route path="/analysis" element={<Analysis/>}/><Route path="/compare" element={<Compare/>}/><Route path="/applications" element={<LegacyApplications/>}/><Route path="*" element={<Navigate to="/" replace/>}/>
      </Routes></Suspense>
    </div></main><footer className="app-footer">ISTG Studio</footer></div>
  </div>;
}
export default function App() { return <BrowserRouter><ExperimentProvider><StudioLayout/></ExperimentProvider></BrowserRouter>; }
