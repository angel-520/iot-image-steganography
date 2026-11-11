import { lazy, Suspense, useEffect, useState } from 'react';
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { Button, Skeleton, Tooltip } from 'antd';
import { AppstoreOutlined, ArrowRightOutlined, BarChartOutlined, ExperimentOutlined, LockOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PictureOutlined, SafetyCertificateOutlined, UnlockOutlined } from '@ant-design/icons';
import { api } from './services/api';
import Home from './pages/Home';
import Embed from './pages/Embed';
import Extract from './pages/Extract';
const Analysis = lazy(() => import('./pages/Analysis'));
const Compare = lazy(() => import('./pages/Compare'));
const Applications = lazy(() => import('./pages/Applications'));
const Multimodal = lazy(() => import('./pages/Multimodal'));
const NetworkLab = lazy(() => import('./pages/NetworkLab'));
const SecretVideo = lazy(() => import('./pages/SecretVideo'));

const navigation = [
  {path:'/',label:'工作台',icon:<AppstoreOutlined/>,caption:'概览与快速开始'},
  {path:'/embed',label:'信息隐藏',icon:<LockOutlined/>,caption:'文本与图片嵌入'},
  {path:'/extract',label:'信息提取',icon:<UnlockOutlined/>,caption:'检测与认证解密'},
  {path:'/multimodal',label:'多模态实验',icon:<ExperimentOutlined/>,caption:'音视频与跨模态'},
  {path:'/network-lab',label:'网络隐写实验室',icon:<SafetyCertificateOutlined/>,caption:'隐蔽信道与流量分析'},
  {path:'/secret-video',label:'SecretVideo 通话',icon:<ExperimentOutlined/>,caption:'多人实时视频隐写'},
  {path:'/analysis',label:'安全性分析',icon:<SafetyCertificateOutlined/>,caption:'图像取证与实验'},
  {path:'/compare',label:'算法对比',icon:<BarChartOutlined/>,caption:'多算法同条件对比'},
  {path:'/applications',label:'应用场景',icon:<PictureOutlined/>,caption:'信息隐藏的应用方向'},
];

function StudioLayout() {
  const location = useLocation();
  const [collapsed,setCollapsed] = useState(false);
  const [health,setHealth] = useState<'loading'|'ok'|'offline'>('loading');
  useEffect(() => { let active=true; const check=()=>{void api.get('/health').then(()=>{if(active)setHealth('ok');}).catch(()=>{if(active)setHealth('offline');});}; check();const id=window.setInterval(check,30_000);return()=>{active=false;window.clearInterval(id);}; },[]);
  const current = navigation.find(item=>item.path===location.pathname) ?? navigation[0];
  const immersive = location.pathname === '/applications';
  return <div className={`studio-shell ${collapsed?'nav-collapsed':''}`}>
    <aside className="sidebar">
      <Link to="/" className="brand" aria-label="ISTG Studio 首页"><div className="brand-mark"><span/><span/><span/><span/></div><div className="brand-copy"><strong>ISTG <em>Studio</em></strong><span>多模态隐写安全平台</span></div></Link>
      <div className="nav-caption">实验工作空间</div>
      <nav aria-label="主导航">{navigation.map(item=><NavLink key={item.path} to={item.path} aria-label={item.label} end={item.path==='/'} className={({isActive})=>`navigation-item ${isActive?'active':''}`}><span className="nav-icon">{item.icon}</span><span className="nav-text"><strong>{item.label}</strong><small>{item.caption}</small></span><span className="nav-arrow"><ArrowRightOutlined/></span></NavLink>)}</nav>
      <div className="sidebar-bottom"><div className="sidebar-footnote">多模态信息隐藏与分析<span>ISTG Studio · 2.0</span></div></div>
    </aside>
    <div className="studio-main"><header className="app-header"><div className="header-left"><Tooltip title={collapsed?'展开导航':'折叠导航'}><Button type="text" aria-label={collapsed?'展开导航':'折叠导航'} onClick={()=>setCollapsed(!collapsed)} icon={collapsed?<MenuUnfoldOutlined/>:<MenuFoldOutlined/>}/></Tooltip><span className="header-divider"/><div className="breadcrumb">工作空间 <span>/</span> <strong>{current.label}</strong></div></div><div className="header-status" role="status"><span className={`connection-dot ${health}`}/><span>{health==='ok'?'已连接':health==='offline'?'连接中断':'正在连接'}</span></div></header>
      <main className={`page-content${immersive?' page-content--immersive':''}`}><div className={`route-view${immersive?' route-view--immersive':''}`} key={location.pathname}><Suspense fallback={<Skeleton active paragraph={{rows:10}}/>}><Routes><Route path="/" element={<Home/>}/><Route path="/embed" element={<Embed/>}/><Route path="/extract" element={<Extract/>}/><Route path="/multimodal" element={<Multimodal/>}/><Route path="/network-lab" element={<NetworkLab/>}/><Route path="/secret-video" element={<SecretVideo/>}/><Route path="/analysis" element={<Analysis/>}/><Route path="/compare" element={<Compare/>}/><Route path="/applications" element={<Applications/>}/><Route path="*" element={<Home/>}/></Routes></Suspense></div></main>
      <footer className="app-footer"><span>ISTG Studio</span><span><ExperimentOutlined/> 多模态信息隐写与安全攻防</span></footer>
    </div>
  </div>;
}
export default function App(){return <BrowserRouter><StudioLayout/></BrowserRouter>;}
