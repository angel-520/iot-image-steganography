import { useMemo, useState } from 'react';
import { Alert, Button, Card, Checkbox, Image, Input, InputNumber, Segmented, Select, Space, Table, Tag, message } from 'antd';
import { BarChartOutlined, DownloadOutlined, ExperimentOutlined, PlayCircleOutlined } from '@ant-design/icons';
import type { EChartsOption } from 'echarts';
import type { TableProps } from 'antd';
import { ImageUploader } from '../components/ImageUploader';
import { ParameterPanel } from '../components/ParameterPanel';
import { ResultCharts } from '../components/ResultCharts';
import { JobStatus } from '../components/JobStatus';
import { useAlgorithms } from '../hooks/useAlgorithms';
import { useJob } from '../hooks/useJob';
import type { AlgorithmParams, Artifact, Encryption, UploadedImage } from '../types';
import { bytes, Exports, Measures, metric, milliseconds, numberOf, percent, StatusTag } from '../components/analysis/common';
import type { Numeric } from '../components/analysis/common';
import '../components/analysis/analysis.css';

interface CompareRow {
  algorithm:string;status:string;error?:string;capacity_bytes?:number;packet_bytes?:number;
  quality?:{mse:Numeric;psnr:Numeric;ssim:Numeric};
  timings?:{embed_seconds:number;extract_seconds:number;total_seconds:number};
  recovery?:{exact_match:boolean;ber:number|null};stego?:Artifact;
  benchmark?:{all_recovered:boolean;embed:{mean_seconds:number|null;std_seconds:number|null};extract:{mean_seconds:number|null;std_seconds:number|null}};
  robustness?:{recovered_count:number;attack_count:number;results:{status:string;ber:number|null;exact_match:boolean;attack:{type:string};file?:Artifact}[]};
}

type ChartMetric = 'psnr'|'ssim'|'time'|'capacity';
const attackLabels:Record<string,string>={jpeg:'JPEG 重压缩',gaussian_noise:'高斯噪声',crop:'裁剪',resize:'缩放'};

export function Compare() {
  const {algorithms,error}=useAlgorithms();
  const [cover,setCover]=useState<UploadedImage>();const [secret,setSecret]=useState<UploadedImage>();
  const [dataType,setDataType]=useState<'text'|'image'>('text');const [text,setText]=useState('');
  const [selected,setSelected]=useState(['sequential_lsb','lsb_matching','dct','dwt']);
  const [parameterMap,setParameterMap]=useState<Record<string,AlgorithmParams>>({});
  const [encryption,setEncryption]=useState<Encryption>('none');const [password,setPassword]=useState('');const [confirmation,setConfirmation]=useState('');
  const [repeats,setRepeats]=useState(1);const [testRobustness,setTestRobustness]=useState(false);const [chartMetric,setChartMetric]=useState<ChartMetric>('psnr');
  const [chartKind,setChartKind]=useState<'bar'|'line'>('bar');
  const job=useJob();
  const rows=(job.result?.rows??[]) as CompareRow[];
  const generalAlgorithms=algorithms.filter(algorithm=>algorithm.mode!=='bitplanes'&&algorithm.id!=='lsb_msb');
  const selectedAlgorithms=generalAlgorithms.filter(algorithm=>selected.includes(algorithm.id));
  const names=useMemo(()=>Object.fromEntries(algorithms.map(algorithm=>[algorithm.id,algorithm.name])),[algorithms]);
  const isEncrypted=encryption!=='none';
  const passwordValid=!isEncrypted||(password.length>0&&password===confirmation);
  const scatterValid=!selected.includes('scatter_lsb')||Boolean(parameterMap.scatter_lsb?.key);
  const canRun=Boolean(cover&&selected.length&&(dataType==='text'?text.length>0:secret)&&passwordValid&&scatterValid);
  const run=async()=>{
    if(!passwordValid){message.warning('请输入非空密码，并确认两次密码一致。');return;}
    if(!scatterValid){message.warning('Random / Scatter LSB 需要已知散布密钥。');return;}
    const result=await job.run('/compare',{
      cover_id:cover?.id,data_type:dataType,text:dataType==='text'?text:undefined,secret_id:dataType==='image'?secret?.id:undefined,
      image_mode:'file',secret_bits:2,algorithm:selected[0],params:parameterMap[selected[0]]??{},
      algorithms:selected,algorithm_params:Object.fromEntries(selected.map(id=>[id,parameterMap[id]??{}])),
      encryption,password:isEncrypted?password:undefined,confirm_password:isEncrypted?confirmation:undefined,repeats,
      attacks:testRobustness?[{type:'jpeg',quality:85},{type:'gaussian_noise',sigma:2,seed:0},{type:'crop',ratio:0.9},{type:'resize',scale:0.75}]:[],
    });
    if(result){setPassword('');setConfirmation('');}
  };
  const successful=rows.filter(row=>row.status==='ok');
  const chartSettings={psnr:{label:'PSNR',unit:'dB'},ssim:{label:'SSIM',unit:''},time:{label:'平均耗时',unit:'ms'},capacity:{label:'有效容量',unit:'KiB'}};
  const chartData=(row:CompareRow):number|null=>chartMetric==='psnr'?numberOf(row.quality?.psnr):chartMetric==='ssim'?numberOf(row.quality?.ssim):chartMetric==='capacity'?(row.capacity_bytes===undefined?null:row.capacity_bytes/1024):numberOf(row.benchmark?.embed.mean_seconds??row.timings?.embed_seconds);
  const option:EChartsOption={
    color:['#159f92','#335e84'],tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
    legend:chartMetric==='time'?{bottom:0}:undefined,
    grid:{left:60,right:25,top:35,bottom:chartMetric==='time'?90:70},
    xAxis:{type:'category',data:rows.map(row=>names[row.algorithm]??row.algorithm),axisLabel:{interval:0,rotate:15,fontSize:13}},
    yAxis:{type:'value',name:chartSettings[chartMetric].unit,min:chartMetric==='ssim'?-1:0,max:chartMetric==='ssim'?1:undefined},
    series:chartMetric==='time'?[
      {name:'嵌入',type:chartKind,data:rows.map(row=>{const value=chartData(row);return value===null?null:value*1000;}),...(chartKind==='bar'?{barMaxWidth:38,itemStyle:{borderRadius:[4,4,0,0]}}:{symbolSize:7,connectNulls:false})},
      {name:'提取',type:chartKind,data:rows.map(row=>{const value=numberOf(row.benchmark?.extract.mean_seconds??row.timings?.extract_seconds);return value===null?null:value*1000;}),...(chartKind==='bar'?{barMaxWidth:38,itemStyle:{borderRadius:[4,4,0,0]}}:{symbolSize:7,connectNulls:false})},
    ]:[{name:chartSettings[chartMetric].label,type:chartKind,data:rows.map(chartData),...(chartKind==='bar'?{barMaxWidth:64,itemStyle:{borderRadius:[5,5,0,0]}}:{symbolSize:7,connectNulls:false})}],
  };
  const columns:TableProps<CompareRow>['columns']=[
    {title:'算法',dataIndex:'algorithm',fixed:'left',width:195,render:(algorithm:string)=>names[algorithm]??algorithm},
    {title:'状态',dataIndex:'status',width:115,render:(status:string)=><StatusTag status={status}/>},
    {title:'PSNR / dB',width:110,render:(_,row)=>metric(row.quality?.psnr,3)},
    {title:'SSIM',width:100,render:(_,row)=>metric(row.quality?.ssim,6)},
    {title:'MSE',width:100,render:(_,row)=>metric(row.quality?.mse,5)},
    {title:'有效容量',dataIndex:'capacity_bytes',width:110,render:(value:number)=>bytes(value)},
    {title:'平均嵌入',width:110,render:(_,row)=>milliseconds(row.benchmark?.embed.mean_seconds??row.timings?.embed_seconds)},
    {title:'平均提取',width:110,render:(_,row)=>milliseconds(row.benchmark?.extract.mean_seconds??row.timings?.extract_seconds)},
    {title:'恢复一致性',width:130,render:(_,row)=>row.recovery?<Space direction="vertical" size={0}><Tag color={row.recovery.exact_match?'success':'error'}>{row.recovery.exact_match?'逐字节一致':'数据不一致'}</Tag><span className="analysis-ber-tag">BER {percent(row.recovery.ber)}</span></Space>:'—'},
    {title:'结果',width:140,render:(_,row)=>row.stego?<Space><Button size="small" icon={<DownloadOutlined/>} href={row.stego.download_url??row.stego.url}>PNG</Button>{row.stego.url&&<Image width={36} height={28} style={{objectFit:'cover'}} src={row.stego.url} alt={`${names[row.algorithm]}隐写结果`}/>}</Space>:<span className="analysis-table-error">{row.error??'无有效输出'}</span>},
  ];
  return <div className="analysis-workspace"><div className="analysis-heading"><div><h1>算法对比</h1></div></div>{error&&<Alert type="error" showIcon message={error}/>}
    <div className="analysis-two"><Card className="analysis-page-card" title="01 · 载体与秘密信息"><ImageUploader label="载体图片" value={cover} onChange={value=>{setCover(value);setPassword('');setConfirmation('');job.reset();}} role="cover"/>
      <div className="analysis-field analysis-spacer"><Segmented block value={dataType} options={[{label:'秘密文本',value:'text'},{label:'秘密图片文件',value:'image'}]} onChange={value=>{setDataType(value as 'text'|'image');setPassword('');setConfirmation('');job.reset();}}/></div>
      {dataType==='text'?<Input.TextArea value={text} onChange={event=>setText(event.target.value)} autoSize={{minRows:5,maxRows:12}} placeholder="输入用于对比的秘密文本" aria-label="算法对比秘密文本"/>:<ImageUploader label="秘密图片" value={secret} onChange={value=>{setSecret(value);setPassword('');setConfirmation('');job.reset();}} role="secret"/>}
      
    </Card>
    <Card className="analysis-page-card" title="02 · 共同实验条件"><div className="analysis-field"><label className="analysis-label">认证加密方式</label><Select className="analysis-full" value={encryption} onChange={value=>{setEncryption(value);setPassword('');setConfirmation('');}} options={[{label:'不加密',value:'none'},{label:'AES-256-GCM',value:'AES-256-GCM'},{label:'ChaCha20-Poly1305',value:'ChaCha20-Poly1305'}]}/></div>
      {isEncrypted&&<div className="analysis-inline-form"><div className="analysis-field"><label className="analysis-label">密码</label><Input.Password value={password} onChange={event=>setPassword(event.target.value)} autoComplete="new-password" placeholder="输入加密密码" aria-label="对比实验加密密码"/></div><div className="analysis-field"><label className="analysis-label">确认密码</label><Input.Password value={confirmation} onChange={event=>setConfirmation(event.target.value)} autoComplete="new-password" placeholder="再次输入密码" status={confirmation&&password!==confirmation?'error':undefined} aria-label="对比实验确认密码"/></div></div>}
      <div className="analysis-field"><label className="analysis-label">性能重复次数</label><InputNumber min={1} max={3} value={repeats} onChange={value=>setRepeats(value??1)} addonAfter="次" className="analysis-full"/></div>
      <Checkbox checked={testRobustness} onChange={event=>setTestRobustness(event.target.checked)}>同时运行四类鲁棒性测试</Checkbox>{testRobustness&&<p className="analysis-note">JPEG Q85 · 噪声 σ2 · 裁剪 90% · 缩放 75%</p>}
      
    </Card></div>
    <Card className="analysis-page-card" title="03 · 算法与独立参数"><div className="analysis-field"><label className="analysis-label">选择比较算法</label><Select mode="multiple" className="analysis-full" aria-label="选择比较算法" value={selected} onChange={setSelected} options={generalAlgorithms.map(algorithm=>({label:algorithm.name,value:algorithm.id,disabled:!algorithm.available}))}/></div>
      <div className="analysis-parameter-grid">{selectedAlgorithms.map(algorithm=><div key={algorithm.id} className="analysis-parameter-card"><h4>{algorithm.name}</h4><ParameterPanel algorithm={algorithm} advanced value={parameterMap[algorithm.id]??{}} onChange={params=>setParameterMap(current=>({...current,[algorithm.id]:params}))}/></div>)}</div>
      {!scatterValid&&<Alert className="analysis-spacer" type="warning" showIcon message="请为 Random / Scatter LSB 填写已知散布密钥"/>}
      <div className="analysis-top-actions analysis-spacer"><p className="analysis-subtitle">{selected.length} 种算法 · PNG 无损输出 · {repeats} 次重复测试</p><Button type="primary" size="large" icon={<PlayCircleOutlined/>} loading={job.loading} disabled={!canRun} onClick={run}>开始对比实验</Button></div>
    </Card><JobStatus task={job.task} loading={job.loading} error={job.error}/>
    {job.result&&<><Card className="analysis-page-card" title={<Space><ExperimentOutlined/> 实验结果</Space>} extra={<Exports taskId={job.task?.id}/>}><Measures items={[{label:'已比较算法',value:rows.length},{label:'成功运行',value:successful.length},{label:'共用数据包',value:bytes(job.result.packet_bytes)},{label:'加密方式',value:job.result.cipher==='none'?'未加密':String(job.result.cipher??encryption)}]}/><div className="analysis-spacer"><Table<CompareRow> size="small" columns={columns} dataSource={rows} rowKey="algorithm" pagination={false} scroll={{x:1320}} expandable={{expandedRowRender:row=><div className="analysis-stack">{row.error&&<Alert type="error" message={row.error}/>}{row.robustness&&<><p>鲁棒性完整恢复：{row.robustness.recovered_count} / {row.robustness.attack_count}</p><Table size="small" pagination={false} rowKey={(_,index)=>String(index)} dataSource={row.robustness.results} columns={[{title:'扰动',render:(_,attack)=>(attackLabels[attack.attack.type]??attack.attack.type)},{title:'提取状态',dataIndex:'status',render:(status:string)=><StatusTag status={status}/>},{title:'BER',dataIndex:'ber',render:percent},{title:'完整恢复',dataIndex:'exact_match',render:(value:boolean)=>value?'是':'否'},{title:'扰动图',render:(_,attack)=>attack.file?<Button size="small" href={attack.file.download_url??attack.file.url}>下载图片</Button>:'—'}]}/></>}</div>,rowExpandable:row=>Boolean(row.robustness||row.error)}}/></div>
      
    </Card><Card className="analysis-page-card" title={<Space><BarChartOutlined/> 指标可视化</Space>} extra={<Space wrap><Segmented aria-label="图表类型" value={chartKind} onChange={value=>setChartKind(value as 'bar'|'line')} options={[{label:'柱状图',value:'bar'},{label:'折线图',value:'line'}]}/><Segmented aria-label="比较指标" value={chartMetric} onChange={value=>setChartMetric(value as ChartMetric)} options={[{label:'PSNR',value:'psnr'},{label:'SSIM',value:'ssim'},{label:'耗时',value:'time'},{label:'容量',value:'capacity'}]}/></Space>}><ResultCharts option={option} height={360}/></Card></>}
  </div>;
}

export default Compare;
