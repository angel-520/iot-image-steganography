import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { HandoffNotice, useExperimentHandoff } from '../hooks/useExperiments';
import { Alert, Button, Card, Descriptions, Input, InputNumber, Segmented, Select, Space, Table, Tag, message } from 'antd';
import { CopyOutlined, DownloadOutlined, ScanOutlined, UnlockOutlined } from '@ant-design/icons';
import { AlgorithmSelector } from '../components/AlgorithmSelector';
import { ImageUploader } from '../components/ImageUploader';
import { ImageViewer } from '../components/ImageViewer';
import { JobStatus } from '../components/JobStatus';
import { defaultParams, ParameterPanel } from '../components/ParameterPanel';
import { useAlgorithms } from '../hooks/useAlgorithms';
import { useJob } from '../hooks/useJob';
import { downloadUrl, exportUrl, fileUrl, formatBytes } from '../services/api';
import type { AlgorithmParams, UploadedImage } from '../types';

interface ExtractionMatch { algorithm: string; attempt: number; packet_length: number; encrypted: boolean }

export default function Extract(){
  const handoff = useExperimentHandoff();
  const navigate = useNavigate();
  const {algorithms,error:algorithmsError}=useAlgorithms();
  const [image,setImage]=useState<UploadedImage | undefined>(handoff.experiment?.stego);
  const [mode,setMode]=useState<'manual'|'auto'>('manual');
  const [algorithm,setAlgorithm]=useState(handoff.experiment?.algorithm ?? 'sequential_lsb');
  const [params,setParams]=useState<AlgorithmParams>(handoff.experiment?.params ?? {});
  const [password,setPassword]=useState('');
  const [selectedAlgorithms,setSelectedAlgorithms]=useState<string[]>([]);
  const [attempts,setAttempts]=useState(64);
  const [knownKey,setKnownKey]=useState('');
  const [candidateJson,setCandidateJson]=useState('');
  const job=useJob();
  useEffect(() => {
    if (job.failureCode !== 'file_not_found') return;
    setImage(undefined); setPassword(''); setKnownKey(''); setParams(current => Object.fromEntries(Object.entries(current).filter(([key]) => key !== 'key')));
    handoff.expire(); job.reset();
  }, [job.failureCode]);
  const clearCredentials=()=>{setPassword('');setKnownKey('');setCandidateJson('');};
  const matches=(job.result?.matches??[]) as ExtractionMatch[];
  const selected=algorithms.find(item=>item.id===algorithm);
  useEffect(()=>{if(selected)setParams(current=>({...defaultParams(selected),...current}));},[selected]);
  useEffect(()=>{if(algorithms.length)setSelectedAlgorithms(current=>current.length?current:algorithms.filter(item=>item.available).map(item=>item.id));},[algorithms]);
  const execute=async()=>{
    if(!image)return;
    let candidates:Record<string,unknown>={};
    if(candidateJson.trim()){
      try{const parsed:unknown=JSON.parse(candidateJson);if(typeof parsed!=='object'||parsed===null||Array.isArray(parsed))throw new Error();candidates=parsed as Record<string,unknown>;}catch{void message.error('参数候选必须是合法的 JSON 对象。');return;}
    }
    if(knownKey)candidates.scatter_lsb=[{key:knownKey,channels:'RGB',bit_depth:1}];
    const result=await job.run('/extract',{image_id:image.id,mode,...(password?{password}:{}),...(mode==='manual'?{algorithm,params}:{algorithms:selectedAlgorithms,parameter_candidates:candidates,max_attempts:attempts})});
    if(result?.status==='ok'){setPassword('');void message.success('秘密信息已成功恢复。');}
  };
  const status=job.result?.status as string|undefined;
  const stateDescriptions:Record<string,{type:'info'|'warning'|'error';title:string;description:string}>={
    no_match:{type:'info',title:'未检测到有效数据包',description:'请检查算法和提取参数，或重新上传图片。'},
    password_required:{type:'warning',title:'检测到加密数据，需要密码',description:'请输入加密密码后重新提取。'},
    authentication_failed:{type:'error',title:'认证解密失败',description:'请检查密码，或重新上传未修改的隐写图片。'},
    extraction_failed:{type:'error',title:'未能提取隐藏数据',description:'请检查隐写算法、通道、位数、散布密钥是否一致，以及图片是否经过有损处理。'},
    invalid_packet:{type:'error',title:'数据包校验失败',description:'数据包格式不匹配或已损坏。'},
  };
  const failure=status?stateDescriptions[status]:undefined;
  const canRun=image&&(mode==='auto'?selectedAlgorithms.length>0:selected?.available&&!selected.parameters.some(p=>p.name==='key'&&p.required&&!params.key));
  return <div><div className="page-heading"><div><h1>信息提取</h1></div>{handoff.experiment && image?.id === handoff.experiment.stego.id && <Button onClick={() => navigate('/embed', { state: { experimentId: handoff.experiment!.id } })}>返回实验结果</Button>}</div>
    <HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />
    {handoff.experiment && image?.id === handoff.experiment.stego.id && (handoff.experiment.encrypted || handoff.experiment.algorithm === 'scatter_lsb') && <p className="handoff-guidance">{handoff.experiment.algorithm === 'scatter_lsb' ? '请重新填写散布位置密钥。' : ''}{handoff.experiment.encrypted ? '此图片已加密，请填写解密密码。' : ''}</p>}
    {algorithmsError&&<Alert type="error" message={algorithmsError} showIcon/>}
    <div className="workspace-grid"><div className="configuration-column"><Card title="隐写图像"><ImageUploader hideLabel label="待提取图片" value={image} onChange={value=>{setImage(value);handoff.detach();handoff.dismiss();clearCredentials();setParams(defaultParams(selected));job.reset();}} role="stego" disabled={job.loading}/></Card><Card title="提取设置"><Segmented block value={mode} onChange={value=>{setMode(value as 'manual'|'auto');clearCredentials();setParams(defaultParams(selected));job.reset();}} options={[{value:'manual',label:'手动提取'},{value:'auto',label:'自动识别'}]}/>
      <div className="form-section">{mode==='manual'?<><AlgorithmSelector algorithms={algorithms} value={algorithm} onChange={value=>{setAlgorithm(value);setParams(defaultParams(algorithms.find(item=>item.id===value)));clearCredentials();job.reset();}}/><ParameterPanel algorithm={selected} value={params} onChange={setParams}/></>:<><label className="field-label">待尝试算法</label><Select aria-label="待尝试算法" mode="multiple" className="full-width" value={selectedAlgorithms} onChange={setSelectedAlgorithms} options={algorithms.map(item=>({value:item.id,label:item.name,disabled:!item.available}))}/><div className="two-field-grid"><div><label className="field-label">最大尝试次数</label><InputNumber aria-label="最大尝试次数" min={1} max={64} precision={0} value={attempts} onChange={value=>setAttempts(value??64)}/></div><div><label className="field-label">已知散布密钥（可选）</label><Input.Password aria-label="已知散布密钥" value={knownKey} onChange={e=>setKnownKey(e.target.value)} autoComplete="new-password"/></div></div><label className="field-label">高级参数候选（JSON，可选）</label><Input.TextArea aria-label="高级参数候选" rows={3} value={candidateJson} onChange={e=>setCandidateJson(e.target.value)} placeholder={'{"sequential_lsb": {"bit_depth": [1, 2], "channels": ["RGB", "B"]}}'}/></>}
        <label className="field-label">解密密码（加密数据需要）</label><Input.Password aria-label="解密密码" value={password} onChange={e=>setPassword(e.target.value)} placeholder="未加密时留空" autoComplete="new-password"/>
      </div></Card><JobStatus task={job.task} loading={job.loading} error={job.error}/><Button data-testid="extract-submit" type="primary" size="large" block disabled={!canRun||job.loading} loading={job.loading} icon={mode==='auto'?<ScanOutlined/>:<UnlockOutlined/>} onClick={()=>void execute()}>{mode==='auto'?'自动检测并提取':'开始提取'}</Button></div>
      <aside className="preview-column"><Card title="提取结果" className="extraction-result-card">
        {!job.result&&!job.loading&&<div className="extraction-placeholder"><div className="placeholder-icon"><UnlockOutlined/></div><h3>等待提取结果</h3></div>}
        {failure&&<Alert type={failure.type} showIcon message={failure.title} description={failure.description}/>} 
        {status==='ok'&&<div className="extracted-content"><div className="result-state"><span className="success-title">秘密信息恢复成功</span><Tag color={job.result?.authenticated?'success':'default'}>{job.result?.authenticated?'AEAD 认证通过':'未加密数据'}</Tag></div>
          {job.result?.data_type==='file'?<><p>原始文件已完整恢复。</p><Button type="primary" icon={<DownloadOutlined/>} href={downloadUrl(job.result.file)}>下载原始文件</Button></>:job.result?.data_type==='text'?<><Input.TextArea aria-label="恢复的秘密文本" className="recovered-text" readOnly rows={9} value={String(job.result.text??'')}/><Space wrap><Button icon={<CopyOutlined/>} onClick={()=>{void navigator.clipboard.writeText(String(job.result?.text??'')).then(()=>message.success('已复制文本')).catch(()=>message.warning('请手动选择并复制文本'));}}>复制文本</Button>{job.result.file&&<Button type="primary" icon={<DownloadOutlined/>} href={downloadUrl(job.result.file)}>下载 TXT</Button>}</Space></>:<><ImageViewer src={fileUrl(job.result?.preview??job.result?.file)} title={job.result?.data_type==='image_pixels'?'恢复图片（有损）':'恢复的原始图片'}/>{job.result?.file&&<Button type="primary" icon={<DownloadOutlined/>} href={downloadUrl(job.result.file)}>下载恢复图片</Button>}</>}
          <details className="result-details-disclosure"><summary>文件详情</summary><Descriptions size="small" column={1} className="result-details" items={[{key:'kind',label:'数据类型',children:({text:'文本',file:'文件',image_file:'图片文件',image_pixels:'图片（有损）'} as Record<string,string>)[String(job.result?.data_type)]??job.result?.data_type},{key:'bytes',label:'ISTG 数据包',children:formatBytes(job.result?.packet_length)},{key:'format',label:'编码 / 文件',children:job.result?.metadata?.encoding??job.result?.metadata?.filename??'RGB 像素数据'}]}/></details>
        </div>}
        {job.task&&job.result&&<div className="result-export"><Button href={exportUrl(job.task.id)} icon={<DownloadOutlined/>}>导出提取报告</Button></div>}
      </Card>{matches.length>0&&<Card title="有效包匹配"><Table<ExtractionMatch> size="small" rowKey={(row)=>`${row.algorithm}-${row.attempt}`} pagination={false} dataSource={matches} columns={[{title:'算法',dataIndex:'algorithm'},{title:'包长度',dataIndex:'packet_length',render:value=>formatBytes(value)},{title:'加密',dataIndex:'encrypted',render:value=>value?'是':'否'}]}/></Card>}</aside>
    </div>
  </div>;
}
