import { Alert, Button, Card, Skeleton, Tag } from 'antd';
import { Link } from 'react-router-dom';
import { ArrowRightOutlined, BarChartOutlined, ExperimentOutlined, FileImageOutlined, LockOutlined, SafetyCertificateOutlined, UnlockOutlined } from '@ant-design/icons';
import { useAlgorithms } from '../hooks/useAlgorithms';

const tools=[
  {path:'/embed',icon:<LockOutlined/>,title:'信息隐藏',en:'EMBED',text:'把秘密放进图像，让信息多一层保护。',tags:'文本 / 图片 · 可选认证加密',color:'teal'},
  {path:'/extract',icon:<UnlockOutlined/>,title:'信息提取',en:'EXTRACT',text:'从图像找回信息，并验证数据的完整性。',tags:'手动提取 / 自动检测',color:'blue'},
  {path:'/analysis',icon:<SafetyCertificateOutlined/>,title:'安全性分析',en:'ANALYZE',text:'观察位平面、统计痕迹与图像质量。',tags:'位平面 / 统计 / 鲁棒性',color:'purple'},
  {path:'/compare',icon:<BarChartOutlined/>,title:'算法对比',en:'COMPARE',text:'在相同载荷下，比较不同算法的表现。',tags:'质量 / 容量 / 耗时',color:'amber'},
];
export default function Home(){
  const {algorithms,loading,error,reload}=useAlgorithms();
  return <div className="home-page">
    <section className="home-hero"><div className="hero-content"><div className="eyebrow"><span/> MULTIMODAL STEGANOGRAPHY</div><h1>多种载体，<br/>多一层<span>信息。</span></h1><p>在图像、音视频与文本中隐藏信息，<br className="desktop-break"/>观察载体变化，比较恢复效果。</p><div className="hero-actions"><Link to="/embed"><Button type="primary" size="large" icon={<ArrowRightOutlined/>} iconPosition="end">开始隐写实验</Button></Link><Link to="/multimodal"><Button size="large">多模态实验</Button></Link></div></div>
      <div className="hero-visual" aria-hidden="true"><div className="visual-orbit orbit-one"/><div className="visual-orbit orbit-two"/><div className="visual-card visual-back"><div className="visual-card-top"><span/><span/><span/></div><div className="visual-wave"><i/><i/><i/><i/><i/><i/><i/><i/></div></div><div className="visual-card visual-front"><div className="visual-card-top"><span className="visual-image-icon"><FileImageOutlined/></span><strong>图像 · 编码 · 分析</strong><span className="visual-png">PNG</span></div><div className="pixel-landscape"><div className="pixel-sun"/><div className="pixel-mountain m-one"/><div className="pixel-mountain m-two"/><div className="pixel-grid"/></div><div className="visual-caption"><span><LockOutlined/> 隐藏在像素之间</span><b>ISTG v1</b></div></div><div className="visual-badge"><SafetyCertificateOutlined/><span>每一步，都可验证</span></div></div>
    </section>
    <div className="section-heading"><div><h2>选择你的实验</h2></div></div>
    <div className="tool-grid">{tools.map(tool=><Link to={tool.path} className={`tool-card ${tool.color}`} key={tool.path}><div className="tool-top"><span className="tool-icon">{tool.icon}</span><span className="tool-en">{tool.en}</span></div><h3>{tool.title}</h3><p>{tool.text}</p><div className="tool-bottom"><span>{tool.tags}</span><ArrowRightOutlined/></div></Link>)}</div>
    <div className="home-bottom-grid"><Card className="supported-card" title={<span><ExperimentOutlined/> 隐写算法</span>}>
      {loading?<Skeleton active paragraph={{rows:2}}/>:error?<Alert type="warning" message="暂时无法读取算法列表" description={error} action={<Button size="small" onClick={()=>void reload()}>重试</Button>}/>:<><div className="algorithm-tags">{algorithms.map(item=><Tag key={item.id}>{item.name}</Tag>)}</div><div className="capability-strip"><div><strong>{algorithms.length}</strong><span>隐写算法与模式</span></div><div><strong>2</strong><span>认证加密方式</span></div><div><strong>3</strong><span>秘密数据类型</span></div></div></>}
    </Card><Card title="一次完整的隐写实验" className="workflow-card"><div className="workflow-list">{[{n:'01',t:'选择载体与秘密信息',d:'文字、图片文件或图片高位数据'},{n:'02',t:'配置算法与加密',d:'选择算法，设置加密方式'},{n:'03',t:'提取并验证恢复结果',d:'上传隐写图片，找回文字或图片'}].map(step=><div className="workflow-step" key={step.n}><span>{step.n}</span><div><strong>{step.t}</strong><small>{step.d}</small></div></div>)}</div></Card></div>
  </div>;
}
