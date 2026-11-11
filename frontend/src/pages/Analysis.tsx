import { useEffect, useState } from 'react';
import { experimentPath, HandoffNotice, useExperimentHandoff } from '../hooks/useExperiments';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Checkbox, Descriptions, Empty, Image, Input, InputNumber,
  Progress, Radio, Select, Space, Table, Tabs, Tag, Typography, message,
} from 'antd';
import {
  AppstoreOutlined, BarChartOutlined, DeleteOutlined, ExperimentOutlined,
  FileSearchOutlined, PlayCircleOutlined, ScanOutlined,
} from '@ant-design/icons';
import type { EChartsOption } from 'echarts';
import type { TableProps } from 'antd';
import { AlgorithmSelector } from '../components/AlgorithmSelector';
import { ImageUploader } from '../components/ImageUploader';
import { ParameterPanel } from '../components/ParameterPanel';
import { ResultCharts } from '../components/ResultCharts';
import { RobustnessPanel } from '../components/analysis/RobustnessPanel';
import { JobStatus } from '../components/JobStatus';
import { useAlgorithms } from '../hooks/useAlgorithms';
import { useJob } from '../hooks/useJob';
import type { Algorithm, AlgorithmParams, Artifact, UploadedImage } from '../types';
import { bytes, Exports, Measures, metric, milliseconds, percent, RecoveredContent, StatusTag } from '../components/analysis/common';
import type { ExtractResult, Numeric } from '../components/analysis/common';
import '../components/analysis/analysis.css';

interface Match {
  algorithm: string; params: AlgorithmParams; valid: boolean; encrypted: boolean;
  packet_length: number; payload_length: number; authenticated: boolean | null; attempt: number;
}
interface Plane { channel: string; bit: number; image: Artifact; reference?: Artifact; difference?: Artifact; changed_pixels?: number; change_ratio?: number }
interface FrequencyBlock { start: number; stop: number; statistic: number; p_value: number | null; degrees_of_freedom: number; status: string }
interface RSCounts { regular: number; singular: number; unusable: number }
interface StatisticsResult {
  chi_square: {statistic:number;p_value:number|null;degrees_of_freedom:number;status:string;blocks:FrequencyBlock[]};
  rs: {positive:RSCounts;negative:RSCounts;groups:number;score:number|null;discarded_pixels:number};
  histogram: {channels:Record<string,{histogram:number[];reference_histogram?:number[];total_variation_distance?:number;jensen_shannon_distance?:number}>};
}
interface Recovery { status:string;exact_match:boolean;ber:number|null;overlap_ber:number|null;original_length:number;extracted_length:number;original_sha256:string;extracted_sha256:string;bit_errors:number }
interface Timing { mean_seconds:number|null;std_seconds:number|null;throughput_bytes_per_second:number|null }
interface BenchmarkResult { status:string;successful_repeats:number;repeats:number;payload_bytes:number;embed:Timing;extract:Timing;runs:{repeat:number;status:string;embed_seconds:number|null;extract_seconds:number|null;recovery?:Recovery;error?:string}[] }
interface EvaluationRow {label:number;score:number|null;prediction:number|null;status:string;path?:string;name?:string;file?:Artifact;error?:string}
interface EvaluationResult {status:string;TP:number;FP:number;TN:number;FN:number;TPR:number|null;FPR:number|null;Precision:number|null;Recall:number|null;Accuracy:number|null;ROC_AUC:number|null;scored_samples:number;total_samples:number;undetermined_samples:number;error_samples:number;results:EvaluationRow[]}

function MethodFields({algorithms,algorithm,onAlgorithm,params,onParams}:{algorithms:Algorithm[];algorithm:string;onAlgorithm:(id:string)=>void;params:AlgorithmParams;onParams:(value:AlgorithmParams)=>void}) {
  const selected = algorithms.find(item => item.id === algorithm);
  return <><div className="analysis-field"><AlgorithmSelector algorithms={algorithms} value={algorithm} onChange={value=>{onAlgorithm(value);onParams({});}}/></div><ParameterPanel algorithm={selected} value={params} onChange={onParams} advanced/></>;
}

function AutoDetectPanel({algorithms}:{algorithms:Algorithm[]}) {
  const [image,setImage] = useState<UploadedImage>();
  const [selected,setSelected] = useState(['sequential_lsb','lsb_matching','adaptive_edge_lsb','dct','dwt','jsteg_demo']);
  const [candidates,setCandidates] = useState('');
  const [scatterKey,setScatterKey] = useState('');
  const [attempts,setAttempts] = useState(64);
  const [password,setPassword] = useState('');
  const job = useJob();
  const extraction = useJob();
  const matches = (job.result?.matches ?? []) as Match[];
  const detect = async () => {
    if (!image) return;
    let specification:Record<string,unknown> = {};
    if(candidates.trim()) {
      try { const parsed:unknown = JSON.parse(candidates); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(); specification = parsed as Record<string,unknown>; }
      catch { message.error('参数候选必须是 JSON 对象，例如 {"sequential_lsb":{"bit_depth":[1,2]}}'); return; }
    }
    if(selected.includes('scatter_lsb') && scatterKey) specification.scatter_lsb ??= [{key:scatterKey,bit_depth:1,channels:'RGB'}];
    await job.run('/analysis/auto-detect',{image_id:image.id,algorithms:selected,parameter_candidates:specification,max_attempts:attempts});
  };
  const recover = async (match:Match) => {
    if(!image) return;
    if(match.algorithm==='scatter_lsb' && !scatterKey) {message.warning('请填写嵌入时的散布密钥。');return;}
    const params = Object.fromEntries(Object.entries(match.params).filter(([key])=>['bit_depth','channels','edge_threshold','strength'].includes(key))) as AlgorithmParams;
    if(match.algorithm==='scatter_lsb') params.key = scatterKey;
    const result = await extraction.run('/extract',{image_id:image.id,mode:'manual',algorithm:match.algorithm,params,password:password || undefined});
    if(result?.status==='ok') setPassword('');
  };
  const columns:TableProps<Match>['columns'] = [
    {title:'匹配算法',dataIndex:'algorithm',render:(value:string)=>algorithms.find(item=>item.id===value)?.name??value},
    {title:'参数组合',dataIndex:'params',render:(value:AlgorithmParams)=><Typography.Text code style={{fontSize:13,whiteSpace:'normal'}}>{JSON.stringify(value)}</Typography.Text>},
    {title:'数据包',dataIndex:'packet_length',render:(value:number)=>bytes(value)},
    {title:'认证状态',render:(_,row)=><Tag color={row.encrypted?'gold':'default'}>{row.encrypted?'已加密 · 待认证':'未加密 · 结构有效'}</Tag>},
    {title:'操作',render:(_,row)=><Button size="small" type="link" onClick={()=>{void recover(row);}} loading={extraction.loading}>提取此匹配</Button>},
  ];
  return <div className="analysis-stack"><div className="analysis-two">
    <Card className="analysis-page-card" title="待分析图片"><ImageUploader label="上传待分析的隐写图片" value={image} onChange={value=>{setImage(value);setPassword('');setScatterKey('');setCandidates('');job.reset();extraction.reset();}} role="stego"/></Card>
    <Card className="analysis-page-card" title="搜索范围"><div className="analysis-field"><label className="analysis-label">候选算法</label><Select mode="multiple" className="analysis-full" aria-label="自动分析候选算法" value={selected} onChange={setSelected} options={algorithms.map(item=>({label:item.name,value:item.id,disabled:!item.available}))}/></div>
      <div className="analysis-inline-form"><div><label className="analysis-label">最大尝试次数</label><InputNumber min={1} max={64} value={attempts} onChange={value=>setAttempts(value??64)} aria-label="最大尝试次数"/></div><div><label className="analysis-label">已知散布密钥（可选）</label><Input.Password value={scatterKey} onChange={event=>setScatterKey(event.target.value)} autoComplete="new-password" placeholder="填写嵌入时的散布密钥"/></div></div>
      <div className="analysis-field analysis-spacer analysis-json-area"><label className="analysis-label">高级参数候选 JSON（可选）</label><Input.TextArea value={candidates} onChange={event=>setCandidates(event.target.value)} autoSize={{minRows:3,maxRows:8}} placeholder={'{"sequential_lsb":{"bit_depth":[1,2],"channels":["RGB","B"]}}'} aria-label="高级参数候选 JSON"/></div>
      <Button type="primary" icon={<ScanOutlined/>} onClick={()=>{void detect();}} loading={job.loading} disabled={!image || !selected.length}>开始自动分析</Button>
    </Card>
  </div><JobStatus task={job.task} loading={job.loading} error={job.error}/>
  {job.result && <Card title={`搜索结果 · ${matches.length} 个有效匹配`} extra={<Exports taskId={job.task?.id}/>}>{matches.length ? <><Table rowKey={row=>`${row.algorithm}-${row.attempt}`} columns={columns} dataSource={matches} pagination={{pageSize:6}} scroll={{x:760}}/><div className="analysis-field"><label className="analysis-label">解密密码（如需要）</label><Input.Password value={password} onChange={event=>setPassword(event.target.value)} autoComplete="new-password" placeholder="输入加密时使用的密码"/></div></> : <Empty description="未找到匹配的数据包" image={Empty.PRESENTED_IMAGE_SIMPLE}/>}</Card>}
  <JobStatus task={extraction.task} loading={extraction.loading} error={extraction.error}/><RecoveredContent result={extraction.result as ExtractResult|undefined}/>
  </div>;
}

function BitplanePanel() {
  const [image,setImage]=useState<UploadedImage>(); const [reference,setReference]=useState<UploadedImage>();
  const [channels,setChannels]=useState(['R','G','B']); const [bits,setBits]=useState([0]);
  const [view,setView]=useState<'image'|'reference'|'difference'>('image'); const job=useJob();
  const planes=(job.result?.planes??[]) as Plane[];
  return <div className="analysis-stack"><div className="analysis-two"><Card title="待分析图像"><ImageUploader label="上传待分析图片" value={image} onChange={value=>{setImage(value);job.reset();}} role="stego"/></Card><Card title="参考原图（可选）"><ImageUploader label="上传同尺寸原图，比较位平面" value={reference} onChange={value=>{setReference(value);setView('image');job.reset();}} role="reference"/></Card></div>
    <Card><div className="analysis-controls"><label>颜色通道<Checkbox.Group options={['R','G','B']} value={channels} onChange={values=>setChannels(values.map(String))}/></label><label style={{flex:3}}>位编号 · Bit 0 为最低位<Checkbox.Group options={Array.from({length:8},(_,bit)=>({label:`Bit ${bit}`,value:bit}))} value={bits} onChange={values=>setBits(values.map(Number))}/></label><Space><Button onClick={()=>{setChannels(['R','G','B']);setBits([0,1,2,3,4,5,6,7]);}}>查看全部 24 位</Button><Button type="primary" icon={<AppstoreOutlined/>} disabled={!image||!channels.length||!bits.length} loading={job.loading} onClick={()=>{void job.run('/analysis/bitplanes',{image_id:image?.id,reference_id:reference?.id,channels,bits});}}>生成位平面</Button></Space></div></Card>
    <JobStatus task={job.task} loading={job.loading} error={job.error}/>
    {planes.length>0&&<Card title={`位平面图谱 · ${planes.length} 个`} extra={<Exports taskId={job.task?.id}/>}><div className="analysis-top-actions"><Radio.Group value={view} onChange={event=>setView(event.target.value)} optionType="button" options={[{label:'待分析图',value:'image'},{label:'参考图',value:'reference',disabled:!planes.some(p=>p.reference)},{label:'XOR 差异',value:'difference',disabled:!planes.some(p=>p.difference)}]}/></div><Image.PreviewGroup><div className="analysis-plane-grid">{planes.map(plane=><div className="analysis-plane" key={`${plane.channel}-${plane.bit}`}><div className="analysis-plane-head"><span>{plane.channel} 通道 · Bit {plane.bit}</span>{plane.bit===0&&<Tag color="cyan">LSB</Tag>}</div><div className="analysis-plane-image">{plane[view]?.url?<Image src={plane[view]?.url} alt={`${plane.channel}通道第${plane.bit}位${view==='difference'?'XOR差异':''}`}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无参考图"/>}</div><div className="analysis-plane-foot"><span>{plane.changed_pixels===undefined?'位平面':`变化 ${metric(plane.changed_pixels,0)} 像素`}</span><strong>{plane.change_ratio===undefined?'—':percent(plane.change_ratio)}</strong></div></div>)}</div></Image.PreviewGroup></Card>}
  </div>;
}

function StatisticsPanel() {
  const [image,setImage]=useState<UploadedImage>(); const [reference,setReference]=useState<UploadedImage>();
  const [channel,setChannel]=useState('R');const [blockSize,setBlockSize]=useState(4096);const [mask,setMask]=useState('[1,0,0,1]');const [groupSize,setGroupSize]=useState(4);const job=useJob();
  const result=job.result as StatisticsResult|undefined;
  const run=()=>{let parsed:unknown;try{parsed=JSON.parse(mask);if(!Array.isArray(parsed)||parsed.length!==groupSize||!parsed.every(v=>Number.isInteger(v)&&[-1,0,1].includes(v)))throw new Error();}catch{message.error('RS 掩码须为与组长一致的 -1、0、1 数组。');return;}void job.run('/analysis/steganalysis',{image_id:image?.id,reference_id:reference?.id,channel,block_size:blockSize,group_size:groupSize,mask:parsed});};
  const colors:Record<string,string>={R:'#e0737c',G:'#1b9d86',B:'#4c89d1'};
  const histogram:EChartsOption=result?{tooltip:{trigger:'axis'},legend:{bottom:0},grid:{left:45,right:20,top:20,bottom:65},xAxis:{type:'category',data:Array.from({length:256},(_,i)=>i),name:'像素值'},yAxis:{type:'value',name:'像素数量'},series:Object.entries(result.histogram.channels).flatMap(([name,entry])=>[
    {name:`${name} 待测`,type:'line' as const,data:entry.histogram,showSymbol:false,lineStyle:{width:1.7,color:colors[name]},itemStyle:{color:colors[name]}},
    ...(entry.reference_histogram?[{name:`${name} 参考`,type:'line' as const,data:entry.reference_histogram,showSymbol:false,lineStyle:{width:1,color:colors[name],type:'dashed' as const},itemStyle:{color:colors[name]}}]:[]),
  ])}:{};
  const chi:EChartsOption=result?{tooltip:{trigger:'axis'},grid:{left:40,right:20,top:20,bottom:35},xAxis:{type:'category',data:result.chi_square.blocks.map((_,index)=>index+1),name:'块'},yAxis:{type:'value',min:0,max:1,name:'p 值'},series:[{name:'p 值',type:'line',data:result.chi_square.blocks.map(block=>block.p_value),showSymbol:false,lineStyle:{color:'#149e91'},areaStyle:{color:'#149e9116'},connectNulls:false}]}:{};
  const rs:EChartsOption=result?{tooltip:{trigger:'axis'},legend:{bottom:0},grid:{left:45,right:20,top:20,bottom:55},xAxis:{type:'category',data:['Regular','Singular','Unusable']},yAxis:{type:'value'},series:[{name:'正掩码 M',type:'bar',data:[result.rs.positive.regular,result.rs.positive.singular,result.rs.positive.unusable],itemStyle:{color:'#159f92',borderRadius:[4,4,0,0]}},{name:'负掩码 −M',type:'bar',data:[result.rs.negative.regular,result.rs.negative.singular,result.rs.negative.unusable],itemStyle:{color:'#375b83',borderRadius:[4,4,0,0]}}]}:{};
  return <div className="analysis-stack"><div className="analysis-two"><Card title="待检测图片"><ImageUploader label="上传待检测图片" value={image} onChange={value=>{setImage(value);job.reset();}} role="stego"/></Card><Card title="直方图参考图片（可选）"><ImageUploader label="上传原始图片进行直方图对照" value={reference} onChange={setReference} role="reference"/></Card></div>
    <Card><div className="analysis-controls"><label>分析通道<Select value={channel} onChange={setChannel} options={['R','G','B'].map(value=>({label:`${value} 通道`,value}))}/></label><label>卡方分块像素数<InputNumber min={256} max={65536} step={256} value={blockSize} onChange={value=>setBlockSize(value??4096)}/></label><label>RS 组长<InputNumber min={2} max={16} value={groupSize} onChange={value=>setGroupSize(value??4)}/></label><label>RS 翻转掩码<Input value={mask} onChange={event=>setMask(event.target.value)} aria-label="RS 翻转掩码"/></label><Button type="primary" icon={<BarChartOutlined/>} disabled={!image} loading={job.loading} onClick={run}>分析隐写痕迹</Button></div></Card>
    
    <JobStatus task={job.task} loading={job.loading} error={job.error}/>
    {result&&<><Card title="统计概览" extra={<Exports taskId={job.task?.id}/>}><Measures items={[{label:'卡方统计量',value:metric(result.chi_square.statistic,4)},{label:'p 值',value:metric(result.chi_square.p_value,6),hint:result.chi_square.p_value===null?'无法计算':undefined},{label:'自由度',value:metric(result.chi_square.degrees_of_freedom,0)},{label:'RS 完整分组',value:metric(result.rs.groups,0)}]}/></Card><Card title="RGB 像素直方图"><ResultCharts option={histogram} height={340}/></Card><div className="analysis-two"><Card title="局部分块 p 值"><ResultCharts option={chi} height={285}/></Card><Card title="RS 正负掩码分组"><ResultCharts option={rs} height={285}/></Card></div><Card title="卡方分块明细"><Table size="small" rowKey={row=>String(row.start)} dataSource={result.chi_square.blocks} pagination={{pageSize:8}} columns={[{title:'像素区间',render:(_,row:FrequencyBlock)=>`${row.start}–${row.stop}`},{title:'统计量',dataIndex:'statistic',render:(value:number)=>metric(value,5)},{title:'自由度',dataIndex:'degrees_of_freedom'},{title:'p 值',dataIndex:'p_value',render:(value:number|null)=>metric(value,6)},{title:'状态',dataIndex:'status',render:(value:string)=><StatusTag status={value}/>}]}/></Card></>}
  </div>;
}

function QualityPanel() {
  const handoff = useExperimentHandoff();
  const navigate = useNavigate();
  const [original, setOriginal] = useState<UploadedImage | undefined>(handoff.experiment?.cover);
  const [stego, setStego] = useState<UploadedImage | undefined>(handoff.experiment?.stego);
  const job = useJob();
  const quality = job.result?.quality as { mse: Numeric; psnr: Numeric; ssim: Numeric } | undefined;
  useEffect(() => {
    if (job.failureCode !== 'file_not_found') return;
    setOriginal(undefined); setStego(undefined); handoff.expire(); job.reset();
  }, [job.failureCode]);
  const changeOriginal = (value: UploadedImage | undefined) => { setOriginal(value); job.reset(); handoff.detach(); handoff.dismiss(); };
  const changeStego = (value: UploadedImage | undefined) => { setStego(value); job.reset(); handoff.detach(); handoff.dismiss(); };
  return <div className="analysis-stack">
    <HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />
    <div className="analysis-two"><ImageUploader label="原始载体图片" value={original} onChange={changeOriginal} role="reference" /><ImageUploader label="隐写结果图片" value={stego} onChange={changeStego} role="stego" /></div>
    <div className="result-actions"><Button type="primary" disabled={!original || !stego} loading={job.loading} onClick={() => { void job.run('/analysis/quality', { original_id: original?.id, stego_id: stego?.id }); }}>计算图像质量</Button>{handoff.experiment && <Button onClick={() => navigate(experimentPath(handoff.experiment!), { state: { experimentId: handoff.experiment!.id } })}>返回实验结果</Button>}</div>
    <JobStatus task={job.task} loading={job.loading} error={job.error} />
    {quality && <><Measures items={[{ label: 'PSNR', value: `${metric(quality.psnr)} dB`, hint: '峰值信噪比' }, { label: 'SSIM', value: metric(quality.ssim, 6), hint: '结构相似性' }, { label: 'MSE', value: metric(quality.mse, 6), hint: '均方误差' }]} /><Exports taskId={job.task?.id} /></>}
    <p className="analysis-note">请选择两张尺寸相同的图片。质量指标用于比较图像变化，不代表隐写安全性。</p>
  </div>;
}

function CapacityPanel({algorithms}:{algorithms:Algorithm[]}) {
  const [cover,setCover]=useState<UploadedImage>(); const [algorithm,setAlgorithm]=useState('sequential_lsb');const [params,setParams]=useState<AlgorithmParams>({});const [text,setText]=useState('');const [encryption,setEncryption]=useState('none');const job=useJob();const result=job.result;
  return <div className="analysis-stack"><div className="analysis-two"><ImageUploader label="用于容量分析的载体图片" value={cover} onChange={setCover} role="cover"/><div><MethodFields algorithms={algorithms.filter(a=>a.mode!=='bitplanes')} algorithm={algorithm} onAlgorithm={setAlgorithm} params={params} onParams={setParams}/><div className="analysis-field"><label className="analysis-label">待封装文字</label><Input.TextArea value={text} onChange={event=>setText(event.target.value)} rows={3} placeholder="输入待隐藏的文本"/></div><Select className="analysis-full" value={encryption} onChange={setEncryption} options={[{label:'不加密',value:'none'},{label:'AES-256-GCM',value:'AES-256-GCM'},{label:'ChaCha20-Poly1305',value:'ChaCha20-Poly1305'}]}/></div></div><Button type="primary" disabled={!cover} loading={job.loading} onClick={()=>{void job.run('/analysis/capacity',{cover_id:cover?.id,data_type:'text',text,image_mode:'file',algorithm,params,encryption});}}>分析容量与开销</Button><JobStatus task={job.task} loading={job.loading} error={job.error}/>{result&&<><Measures items={[{label:'有效输入容量',value:bytes(result.capacity_bytes)},{label:'当前封装包',value:bytes(result.used_bytes)},{label:'ISTG 封装开销',value:bytes(result.istg_overhead_bytes)},{label:'加密额外开销',value:bytes(result.encryption_overhead_bytes)},{label:'物理 bpp',value:metric(result.bpp,4)}]}/><Progress percent={Math.min(100,Number(result.usage_percent)||0)} format={()=>`${metric(result.usage_percent,2)}%`} status={result.fits?'normal':'exception'} strokeColor="#159d91"/><Exports taskId={job.task?.id}/></>}</div>;
}

function RecoveryPanel() {
  const [original,setOriginal]=useState<UploadedImage>();const [extracted,setExtracted]=useState<UploadedImage>();const job=useJob();const result=job.result as Recovery|undefined;
  return <div className="analysis-stack"><div className="analysis-two"><ImageUploader label="原始秘密图片" value={original} onChange={setOriginal} role="secret"/><ImageUploader label="提取恢复的图片" value={extracted} onChange={setExtracted} role="reference"/></div><Button type="primary" disabled={!original||!extracted} loading={job.loading} onClick={()=>{void job.run('/analysis/recovery',{original_id:original?.id,extracted_id:extracted?.id});}}>比较文件恢复一致性</Button><JobStatus task={job.task} loading={job.loading} error={job.error}/>{result&&<><Measures items={[{label:'完全一致',value:result.exact_match?'是':'否'},{label:'BER',value:percent(result.ber)},{label:'重叠区域 BER',value:percent(result.overlap_ber)},{label:'错误 bit 数',value:metric(result.bit_errors,0)}]}/><Descriptions bordered size="small" column={1} items={[{key:'before',label:'原始 SHA-256',children:<span className="analysis-hash">{result.original_sha256}</span>},{key:'after',label:'提取 SHA-256',children:<span className="analysis-hash">{result.extracted_sha256}</span>}]}/><Exports taskId={job.task?.id}/></>}</div>;
}

function BenchmarkPanel({algorithms}:{algorithms:Algorithm[]}) {
  const [image,setImage]=useState<UploadedImage>();const [algorithm,setAlgorithm]=useState('sequential_lsb');const [params,setParams]=useState<AlgorithmParams>({});const [text,setText]=useState('');const [repeats,setRepeats]=useState(3);const job=useJob();const result=job.result as BenchmarkResult|undefined;
  return <div className="analysis-stack"><div className="analysis-two"><ImageUploader label="基准测试载体图片" value={image} onChange={setImage} role="cover"/><div><MethodFields algorithms={algorithms.filter(a=>a.mode!=='bitplanes')} algorithm={algorithm} onAlgorithm={setAlgorithm} params={params} onParams={setParams}/><label className="analysis-label">测试文本</label><Input.TextArea value={text} onChange={event=>setText(event.target.value)} rows={3} placeholder="输入用于测试的文本"/><div className="analysis-spacer"><Space><span>重复次数</span><InputNumber min={1} max={5} value={repeats} onChange={value=>setRepeats(value??3)}/></Space></div></div></div><Button type="primary" disabled={!image||!text.trim()} loading={job.loading} icon={<PlayCircleOutlined/>} onClick={()=>{void job.run('/analysis/benchmark',{image_id:image?.id,payload_text:text,algorithm,params,repeats});}}>开始基准测试</Button><JobStatus task={job.task} loading={job.loading} error={job.error}/>{result&&<><Measures items={[{label:'平均嵌入耗时',value:milliseconds(result.embed?.mean_seconds),hint:`标准差 ${milliseconds(result.embed?.std_seconds)}`},{label:'平均提取耗时',value:milliseconds(result.extract?.mean_seconds),hint:`标准差 ${milliseconds(result.extract?.std_seconds)}`},{label:'嵌入吞吐量',value:`${bytes(result.embed?.throughput_bytes_per_second)}/s`},{label:'完整恢复次数',value:`${result.successful_repeats} / ${result.repeats}`} ]}/><Table size="small" rowKey="repeat" dataSource={result.runs} pagination={false} columns={[{title:'轮次',dataIndex:'repeat'},{title:'嵌入',dataIndex:'embed_seconds',render:milliseconds},{title:'提取',dataIndex:'extract_seconds',render:milliseconds},{title:'恢复状态',dataIndex:'status',render:(status:string)=><StatusTag status={status}/>},{title:'BER',render:(_,row)=>percent(row.recovery?.ber)},{title:'说明',dataIndex:'error',ellipsis:true}]}/><Exports taskId={job.task?.id}/></>}</div>;
}

function DatasetPicker({label,files,onChange,role}:{label:string;files:UploadedImage[];onChange:(files:UploadedImage[])=>void;role:string}) {
  return <div><ImageUploader label={`${label} · 点击或拖拽逐张添加`} role={role} value={undefined} disabled={files.length>=10} onChange={image=>{if(image&&files.length<10&&!files.some(file=>file.id===image.id))onChange([...files,image]);}}/><div className="analysis-preview-list">{files.map(file=><div className="analysis-preview-item" key={file.id}><Image src={file.url} width={38} height={32} alt={file.name} style={{objectFit:'cover'}}/><span title={file.name}>{file.name}</span><Button size="small" type="text" icon={<DeleteOutlined/>} aria-label={`移除${file.name}`} onClick={()=>onChange(files.filter(item=>item.id!==file.id))}/></div>)}</div><p className="analysis-note">已添加 {files.length} / 10 张</p></div>;
}

function EvaluationPanel() {
  const [originals,setOriginals]=useState<UploadedImage[]>([]);const [stegos,setStegos]=useState<UploadedImage[]>([]);const [detector,setDetector]=useState('chi_square');const [threshold,setThreshold]=useState(0.5);const job=useJob();const result=job.result as EvaluationResult|undefined;
  const columns:TableProps<EvaluationRow>['columns']=[
    {title:'样本',render:(_,row)=><Space>{row.file?.url&&<Image src={row.file.url} width={36} height={28} style={{objectFit:'cover'}} alt={row.file.name??'评价样本'}/>}<span>{row.file?.name??'已上传图片'}</span></Space>},
    {title:'真实类别',dataIndex:'label',render:(label:number)=><Tag color={label?'cyan':'default'}>{label?'隐写正样本':'原图负样本'}</Tag>},
    {title:'分数',dataIndex:'score',render:(score:number|null)=>metric(score,6)},
    {title:'预测',dataIndex:'prediction',render:(prediction:number|null)=>prediction===null?'无法判定':prediction?'可疑':'低于阈值'},
    {title:'状态',dataIndex:'status',render:(status:string)=><StatusTag status={status}/>},
    {title:'说明',dataIndex:'error',ellipsis:true},
  ];
  return <div className="analysis-stack">
    
    <div className="analysis-two"><DatasetPicker label="原始图片（负样本）" files={originals} onChange={setOriginals} role="reference"/><DatasetPicker label="隐写图片（正样本）" files={stegos} onChange={setStegos} role="stego"/></div>
    <div className="analysis-controls">
      <label>检测器<Select value={detector} onChange={setDetector} options={[{label:'PoV 卡方模型匹配分数',value:'chi_square'},{label:'RS 对称性差异启发分数',value:'rs'}]}/></label>
      <label>判正阈值（分数 ≥ 阈值）<InputNumber min={0} max={1} step={0.05} value={threshold} onChange={value=>setThreshold(value??0.5)}/></label>
      <Button type="primary" disabled={!originals.length||!stegos.length} loading={job.loading} onClick={()=>{void job.run('/analysis/evaluate',{original_ids:originals.map(file=>file.id),stego_ids:stegos.map(file=>file.id),detector,threshold});}}>评价检测器</Button>
    </div><JobStatus task={job.task} loading={job.loading} error={job.error}/>
    {result&&<><Measures items={[{label:'TP / FP / TN / FN',value:`${result.TP} / ${result.FP} / ${result.TN} / ${result.FN}`},{label:'准确率',value:percent(result.Accuracy)},{label:'召回率 / TPR',value:percent(result.Recall)},{label:'FPR',value:percent(result.FPR)},{label:'Precision',value:percent(result.Precision)},{label:'ROC-AUC',value:metric(result.ROC_AUC,5)}]}/><p className="analysis-note">可评分 {result.scored_samples} / {result.total_samples} 个样本 · 无法判定 {result.undetermined_samples} · 错误 {result.error_samples}</p><Table size="small" rowKey={(_,index)=>String(index)} dataSource={result.results} pagination={{pageSize:8}} columns={columns}/><Exports taskId={job.task?.id}/></>}
  </div>;
}

function PerformancePanel({algorithms}:{algorithms:Algorithm[]}) {
  const [search, setSearch] = useSearchParams();
  const panels = ['quality', 'capacity', 'recovery', 'benchmark', 'robustness', 'evaluate'];
  const panel = search.get('panel') ?? 'quality';
  return <Card className="analysis-page-card"><Tabs className="analysis-perf-tabs" activeKey={panels.includes(panel) ? panel : 'quality'} onChange={key => setSearch(current => { current.set('tab', 'performance'); current.set('panel', key); return current; }, { replace: true })} destroyOnHidden items={[
    {key:'quality',label:'图像质量',children:<QualityPanel/>},
    {key:'capacity',label:'容量分析',children:<CapacityPanel algorithms={algorithms}/>},
    {key:'recovery',label:'恢复一致性',children:<RecoveryPanel/>},
    {key:'benchmark',label:'性能基准',children:<BenchmarkPanel algorithms={algorithms}/>},
    {key:'robustness',label:'鲁棒性测试',children:<RobustnessPanel algorithms={algorithms}/>},
    {key:'evaluate',label:'检测器评价',children:<EvaluationPanel/>},
  ]}/></Card>;
}

export function Analysis() {
  const [search, setSearch] = useSearchParams();
  const tabs = ['auto', 'planes', 'statistics', 'performance'];
  const tab = search.get('tab') ?? 'auto';
  const {algorithms,error,loading}=useAlgorithms();
  return <div className="analysis-workspace"><div className="analysis-heading"><div><h1>安全分析</h1></div></div>{error&&<Alert type="error" showIcon message={error}/>}<Tabs className="analysis-tabs" activeKey={tabs.includes(tab) ? tab : 'auto'} onChange={key => setSearch(current => { current.set('tab', key); return current; }, { replace: true })} destroyOnHidden items={[
    {key:'auto',label:<span><FileSearchOutlined/> 自动提取分析</span>,children:<AutoDetectPanel algorithms={algorithms}/>},
    {key:'planes',label:<span><AppstoreOutlined/> 位平面可视化</span>,children:<BitplanePanel/>},
    {key:'statistics',label:<span><BarChartOutlined/> 隐写痕迹检测</span>,children:<StatisticsPanel/>},
    {key:'performance',label:<span><ExperimentOutlined/> 性能与鲁棒性</span>,children:loading?<Card loading/>:<PerformancePanel algorithms={algorithms}/>},
  ]}/></div>;
}

export default Analysis;
