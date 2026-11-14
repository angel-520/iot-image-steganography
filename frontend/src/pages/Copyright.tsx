import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import type { ReactNode } from 'react';
import { Alert, Button, Card, Checkbox, Descriptions, Drawer, Empty, Input, Segmented, Select, Skeleton, Space, Table, Tabs, Tag, Upload, message } from 'antd';
import { ArrowLeftOutlined, CheckCircleOutlined, DownloadOutlined, InboxOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined, UploadOutlined } from '@ant-design/icons';
import { api, errorMessage, formatBytes } from '../services/api';
import '../styles/copyright.css';

interface User { id: string; username: string }
interface Work {
  id: string; title: string; author: string; rights: string; filename: string; size_bytes: number;
  width: number; height: number; created_at: number; owner_username: string;
  can_manage: boolean; can_download: boolean; access_status: 'owner' | 'active' | 'expired' | 'revoked'; content_url: string;
}
interface MarkMetadata {
  work_id: string; title: string; author: string; rights: string; recipient?: string;
  distribution_id?: string; issued_at?: number | string;
}
interface Mark { id: string; work_id: string; recipient: string; created_at: number; filename: string; url: string; download_url: string; metadata: MarkMetadata }
interface Verification { status: 'verified' | 'invalid' | 'not_found'; signature_valid: boolean; binding_valid: boolean; record_matched: boolean; metadata?: MarkMetadata; message: string }
interface Grant { id: string; username: string; expires_at: number | null; can_download: boolean; revoked: boolean; created_at: number }
interface AuditEvent { id: string; action: string; username: string; created_at: number; details: string }
interface Operations { onFailure: (error: unknown) => void; onBusy: (value: boolean) => void }
const prefix = '/copyright';
const maxImageBytes = 16 * 1024 * 1024;
const acceptImages = '.png,.jpg,.jpeg,.bmp,.webp,.tif,.tiff';
const accessText = { owner: '我的作品', active: '授权有效', expired: '授权已到期', revoked: '授权已撤销' };

function time(value?: number | string | null): string {
  if (value === null || value === undefined) return '—';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN');
}
function localDateTime(days = 7) {
  const date = new Date(Date.now() + days * 86_400_000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}
function workPath(id: string) { return prefix + '/works/' + encodeURIComponent(id); }
function useAction({ onFailure, onBusy }: Operations) {
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const run = async <T,>(operation: () => Promise<T>): Promise<T | undefined> => {
    if (pending.current) return undefined;
    pending.current = true; setBusy(true); onBusy(true);
    try { return await operation(); }
    catch (error) { onFailure(error); return undefined; }
    finally { pending.current = false; setBusy(false); onBusy(false); }
  };
  return { busy, run };
}
function useLocalPreview(file?: File) {
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    if (!file) { setUrl(undefined); return; }
    const next = URL.createObjectURL(file); setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return url;
}
function protectedPath(url: string) {
  if (!url.startsWith('/api/copyright/')) throw new Error('作品文件地址无效。');
  return url.slice(4);
}
async function downloadProtected(url: string, filename: string) {
  const { data } = await api.get<Blob>(protectedPath(url), { responseType: 'blob' });
  const objectUrl = URL.createObjectURL(data);
  const link = document.createElement('a'); link.href = objectUrl; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
function imageChosen(file: File, onFailure: Operations['onFailure'], maximum = maxImageBytes) {
  if (file.size > maximum) { onFailure(new Error('图片不能超过 ' + (maximum / 1024 / 1024) + ' MiB。')); return false; }
  if (!/\.(png|jpe?g|bmp|webp|tiff?)$/i.test(file.name)) { onFailure(new Error('请选择 PNG、JPEG、WebP、BMP 或 TIFF 图片。')); return false; }
  return true;
}
function WorkSelect({ works, value, onChange, disabled, ownerOnly = false }: { works: Work[]; value?: string; onChange: (id: string) => void; disabled?: boolean; ownerOnly?: boolean }) {
  const options = works.filter(work => !ownerOnly || work.can_manage);
  return <div className="copyright-field"><label htmlFor="copyright-work-select">选择作品</label><Select id="copyright-work-select" aria-label="选择作品" value={value} disabled={disabled} className="full-width" onChange={onChange} placeholder={options.length ? '选择要操作的作品' : '暂无作品'} options={options.map(work => ({ value: work.id, label: work.title + ' · ' + work.author }))} /></div>;
}
function AuthForm({ onAuthenticated, ...operations }: Operations & { onAuthenticated: (user: User) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState(''); const [password, setPassword] = useState(''); const [confirmation, setConfirmation] = useState('');
  const { busy, run } = useAction(operations);
  const valid = /^[A-Za-z0-9_]{3,32}$/.test(username) && password.length >= 10 && password.length <= 128 && (mode === 'login' || password === confirmation);
  const submit = () => void run(async () => {
    const { data } = await api.post<{ user: User }>(prefix + '/auth/' + mode, { username, password });
    setPassword(''); setConfirmation(''); onAuthenticated(data.user);
  });
  return <Card className="copyright-auth" title="登录后管理作品">
    <Segmented block aria-label="账户操作" value={mode} onChange={value => { setMode(value as 'login' | 'register'); setPassword(''); setConfirmation(''); }} disabled={busy} options={[{ value: 'login', label: '登录' }, { value: 'register', label: '注册' }]} />
    <form className="copyright-form" onSubmit={event => { event.preventDefault(); if (valid) submit(); }}>
      <div className="copyright-field"><label htmlFor="copyright-username">用户名</label><Input id="copyright-username" autoComplete="username" aria-label="版权用户名" maxLength={32} value={username} disabled={busy} onChange={event => setUsername(event.target.value)} placeholder="3–32 位字母、数字或下划线" /></div>
      <div className="copyright-field"><label htmlFor="copyright-password">密码</label><Input.Password id="copyright-password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} aria-label="版权密码" maxLength={128} value={password} disabled={busy} onChange={event => setPassword(event.target.value)} placeholder="至少 10 个字符" /></div>
      {mode === 'register' && <div className="copyright-field"><label htmlFor="copyright-confirm">确认密码</label><Input.Password id="copyright-confirm" aria-label="版权确认密码" autoComplete="new-password" value={confirmation} maxLength={128} disabled={busy} onChange={event => setConfirmation(event.target.value)} placeholder="再次输入密码" status={confirmation && password !== confirmation ? 'error' : undefined} /></div>}
      <Button type="primary" htmlType="submit" loading={busy} disabled={!valid}>{mode === 'login' ? '登录' : '注册并登录'}</Button>
    </form>
  </Card>;
}
function AddWork({ onCreated, onCancel, ...operations }: Operations & { onCreated: (work: Work) => void; onCancel: () => void }) {
  const [file, setFile] = useState<File>(); const [title, setTitle] = useState(''); const [author, setAuthor] = useState(''); const [rights, setRights] = useState('');
  const preview = useLocalPreview(file); const { busy, run } = useAction(operations);
  const ready = file && title.trim() && author.trim() && rights.trim();
  const submit = () => void run(async () => {
    if (!file) return;
    const form = new FormData(); form.append('file', file); form.append('title', title.trim()); form.append('author', author.trim()); form.append('rights', rights.trim());
    const { data } = await api.post<{ work: Work }>(prefix + '/works', form);
    onCreated(data.work); void message.success('作品已添加');
  });
  return <Card title="添加作品" extra={<Button type="text" icon={<ArrowLeftOutlined />} disabled={busy} onClick={onCancel}>返回作品</Button>}>
    <form className="copyright-upload-form" onSubmit={event => { event.preventDefault(); if (ready) submit(); }}>
      <div className="copyright-upload-column"><Upload.Dragger accept={acceptImages} showUploadList={false} disabled={busy} beforeUpload={selected => {
        if (imageChosen(selected, operations.onFailure)) { setFile(selected); if (!title) setTitle(selected.name.replace(/\.[^.]+$/, '').slice(0, 100)); }
        return false;
      }}><p className="ant-upload-drag-icon"><InboxOutlined /></p><p>{file?.name ?? '点击或拖入作品图片'}</p><p className="ant-upload-hint">PNG / JPEG / WebP / BMP / TIFF · 最大 16 MiB</p></Upload.Dragger>{preview && <img className="copyright-local-preview" src={preview} alt="待添加作品预览" />}</div>
      <div className="copyright-form"><div className="copyright-field"><label htmlFor="copyright-title">作品名称</label><Input id="copyright-title" aria-label="作品名称" maxLength={100} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} placeholder="输入作品名称" /></div>
        <div className="copyright-field"><label htmlFor="copyright-author">署名作者</label><Input id="copyright-author" aria-label="署名作者" maxLength={100} value={author} disabled={busy} onChange={event => setAuthor(event.target.value)} placeholder="作者或机构名称" /></div>
        <div className="copyright-field"><label htmlFor="copyright-rights">版权声明</label><Input.TextArea id="copyright-rights" aria-label="版权声明" rows={4} maxLength={1000} value={rights} disabled={busy} onChange={event => setRights(event.target.value)} placeholder="填写作品的使用或转载约定" /></div>
        <Button type="primary" htmlType="submit" icon={<UploadOutlined />} loading={busy} disabled={!ready}>保存作品</Button>
      </div>
    </form>
  </Card>;
}
function WorkViewer({ work, onClose, ...operations }: Operations & { work: Work; onClose: () => void }) {
  const [url, setUrl] = useState<string>(); const [loading, setLoading] = useState(true); const [viewError, setViewError] = useState<string>();
  const { busy, run } = useAction(operations);
  useEffect(() => {
    const abort = new AbortController(); let objectUrl: string | undefined;
    void api.get<Blob>(protectedPath(work.content_url), { responseType: 'blob', signal: abort.signal }).then(({ data }) => {
      if (!abort.signal.aborted) { objectUrl = URL.createObjectURL(data); setUrl(objectUrl); }
    }).catch(error => { if (!abort.signal.aborted) { setViewError(errorMessage(error)); operations.onFailure(error); } }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => { abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [work.id, work.content_url, operations.onFailure]);
  return <Drawer title={work.title} width="min(860px, 100vw)" open onClose={onClose} destroyOnHidden>
    <div className="copyright-stack">{loading ? <Skeleton.Image active className="copyright-preview-skeleton" /> : url ? <img className="copyright-private-preview" src={url} alt={work.title} /> : <Alert type="warning" message={viewError ?? '无法读取作品'} />}
      <Descriptions column={1} size="small" items={[{ key: 'author', label: '署名作者', children: work.author }, { key: 'rights', label: '版权声明', children: work.rights }, { key: 'size', label: '尺寸', children: work.width + ' × ' + work.height }, { key: 'bytes', label: '文件大小', children: formatBytes(work.size_bytes) }, { key: 'created', label: '添加时间', children: time(work.created_at) }]} />
      {work.can_download && <Button type="primary" icon={<DownloadOutlined />} loading={busy} onClick={() => void run(() => downloadProtected(work.content_url + '?download=true', work.filename))}>下载原文件</Button>}
    </div>
  </Drawer>;
}
function Library({ works, loading, onRefresh, onCreated, ...operations }: Operations & { works: Work[]; loading: boolean; onRefresh: () => void; onCreated: (work: Work) => void }) {
  const [adding, setAdding] = useState(false); const [viewing, setViewing] = useState<Work>();
  if (adding) return <AddWork {...operations} onCancel={() => setAdding(false)} onCreated={work => { setAdding(false); onCreated(work); }} />;
  return <><Card title="作品" extra={<Space wrap><Button aria-label="刷新作品" icon={<ReloadOutlined />} onClick={onRefresh} loading={loading}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => setAdding(true)}>添加作品</Button></Space>}>
    <Table<Work> rowKey="id" loading={loading} dataSource={works} locale={{ emptyText: <Empty description="暂无作品" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }} scroll={{ x: 720 }} pagination={{ pageSize: 8 }} columns={[
      { title: '作品', dataIndex: 'title', render: (title: string, work) => <div className="copyright-work-name"><strong>{title}</strong><span>{work.author}</span></div> },
      { title: '所属账户', dataIndex: 'owner_username' },
      { title: '权限', dataIndex: 'access_status', render: (status: Work['access_status']) => <Tag color={status === 'owner' ? 'cyan' : status === 'active' ? 'success' : 'default'}>{accessText[status]}</Tag> },
      { title: '文件大小', dataIndex: 'size_bytes', render: (value: number) => formatBytes(value) },
      { title: '操作', render: (_: unknown, work) => <Button size="small" disabled={!['owner', 'active'].includes(work.access_status)} onClick={() => setViewing(work)}>查看作品</Button> },
    ]} />
  </Card>{viewing && <WorkViewer key={viewing.id} {...operations} work={viewing} onClose={() => setViewing(undefined)} />}</>;
}
function Marks({ works, onOpenLibrary, ...operations }: Operations & { works: Work[]; onOpenLibrary: () => void }) {
  const owned = works.filter(work => work.can_manage); const [selectedId, setSelectedId] = useState<string>(); const [recipient, setRecipient] = useState('');
  const selected = owned.find(work => work.id === selectedId) ?? owned[0];
  const [marks, setMarks] = useState<Mark[]>([]); const [current, setCurrent] = useState<Mark>(); const [revision, setRevision] = useState(0); const [loading, setLoading] = useState(false);
  const { busy, run } = useAction(operations);
  useEffect(() => {
    setMarks([]); if (!selected) return;
    const abort = new AbortController(); setLoading(true);
    void api.get<{ marks: Mark[] }>(workPath(selected.id) + '/marks', { signal: abort.signal }).then(({ data }) => { if (!abort.signal.aborted) setMarks(data.marks); }).catch(error => { if (!abort.signal.aborted) operations.onFailure(error); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [selected?.id, revision, operations.onFailure]);
  useEffect(() => { setCurrent(undefined); }, [selected?.id]);
  const generate = () => void run(async () => {
    if (!selected) return;
    const { data } = await api.post<{ mark: Mark }>(workPath(selected.id) + '/mark', { recipient: recipient.trim() });
    setRevision(value => value + 1); setCurrent(data.mark); void message.success('版权标识已生成');
  });
  if (!selected) return <Card><Empty description="请先添加作品"><Button type="primary" onClick={onOpenLibrary}>前往作品管理</Button></Empty></Card>;
  return <div className="copyright-stack"><Card title="生成版权标识"><div className="copyright-mark-form"><WorkSelect works={owned} value={selected.id} onChange={id => { setSelectedId(id); setRecipient(''); }} disabled={busy} ownerOnly />
    <div className="copyright-field"><label htmlFor="copyright-recipient">分发对象（可选）</label><Input id="copyright-recipient" aria-label="分发对象" value={recipient} onChange={event => setRecipient(event.target.value)} maxLength={100} disabled={busy} placeholder="填写接收者或分发用途" /></div><Button type="primary" icon={<SafetyCertificateOutlined />} loading={busy} onClick={generate}>生成版权标识</Button></div>
    {current && <div className="copyright-mark-result"><Tag color="success">标识已生成</Tag><span className="copyright-subtle">{current.recipient || '通用标识'}</span><Button icon={<DownloadOutlined />} disabled={busy} onClick={() => void run(() => downloadProtected(current.download_url, current.filename))}>下载标识图片</Button></div>}
  </Card><Card title="分发记录"><Table<Mark> rowKey="id" dataSource={marks} loading={loading} scroll={{ x: 640 }} pagination={{ pageSize: 6 }} locale={{ emptyText: '暂无分发记录' }} columns={[
    { title: '分发对象', dataIndex: 'recipient', render: (value: string) => value || '通用标识' },
    { title: '创建时间', dataIndex: 'created_at', render: time },
    { title: '分发编号', dataIndex: 'id', render: (id: string) => <code className="copyright-code">{id}</code> },
    { title: '操作', render: (_: unknown, mark) => <Button size="small" icon={<DownloadOutlined />} disabled={busy} onClick={() => void run(() => downloadProtected(mark.download_url, mark.filename))}>下载图片</Button> },
  ]} /></Card></div>;
}
function Verify({ ...operations }: Operations) {
  const [file, setFile] = useState<File>(); const [result, setResult] = useState<Verification>();
  const preview = useLocalPreview(file); const { busy, run } = useAction(operations);
  const verify = () => void run(async () => {
    if (!file) return; setResult(undefined);
    const form = new FormData(); form.append('file', file);
    const { data } = await api.post<Verification>(prefix + '/verify', form); setResult(data);
  });
  const metadata = result?.metadata;
  return <div className="copyright-verify-grid"><Card title="上传标识图片"><div className="copyright-stack"><Upload.Dragger disabled={busy} accept={acceptImages} showUploadList={false} beforeUpload={selected => { if (imageChosen(selected, operations.onFailure, 64 * 1024 * 1024)) { setFile(selected); setResult(undefined); } return false; }}>
    <p className="ant-upload-drag-icon"><InboxOutlined /></p><p>{file?.name ?? '点击或拖入待验证图片'}</p><p className="ant-upload-hint">最大 64 MiB</p>
  </Upload.Dragger>{preview && <img src={preview} alt="待验证标识图片" className="copyright-local-preview" />}<Button type="primary" loading={busy} disabled={!file} icon={<CheckCircleOutlined />} onClick={verify}>验证版权标识</Button></div></Card>
    <Card title="验证结果">{!result ? <Empty description="上传图片后开始验证" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : <div className="copyright-stack">
      <Alert type={result.status === 'verified' ? 'success' : result.status === 'not_found' ? 'info' : 'warning'} showIcon message={result.message} />
      {result.status !== 'not_found' && <Space wrap><Tag color={result.signature_valid ? 'success' : 'error'}>{result.signature_valid ? '签名有效' : '签名未通过'}</Tag><Tag color={result.binding_valid ? 'success' : 'error'}>{result.binding_valid ? '图像匹配' : '图像不匹配'}</Tag><Tag color={result.record_matched ? 'success' : 'error'}>{result.record_matched ? '记录匹配' : '未匹配记录'}</Tag></Space>}
      {metadata && <Descriptions column={1} size="small" items={[{ key: 'title', label: '作品名称', children: metadata.title }, { key: 'author', label: '署名作者', children: metadata.author }, { key: 'rights', label: '版权声明', children: metadata.rights }, { key: 'recipient', label: '分发对象', children: metadata.recipient || '通用标识' }, { key: 'id', label: '作品编号', children: <code className="copyright-code">{metadata.work_id}</code> }, { key: 'distribution', label: '分发编号', children: <code className="copyright-code">{metadata.distribution_id ?? '—'}</code> }, { key: 'time', label: '签发时间', children: time(metadata.issued_at) }]} />}
    </div>}</Card>
  </div>;
}
const actionText: Record<string, string> = {
  create: '添加作品', view: '查看作品', download: '下载原文件',
  mark: '生成版权标识', view_mark: '查看标识图片', export_mark: '下载标识图片',
  grant: '授予访问权限', revoke: '撤销访问权限',
};
function Access({ works, onOpenLibrary, ...operations }: Operations & { works: Work[]; onOpenLibrary: () => void }) {
  const owned = works.filter(work => work.can_manage); const [selectedId, setSelectedId] = useState<string>(); const selected = owned.find(work => work.id === selectedId) ?? owned[0];
  const [username, setUsername] = useState(''); const [expiry, setExpiry] = useState(() => localDateTime()); const [permanent, setPermanent] = useState(false); const [canDownload, setCanDownload] = useState(false);
  const [grants, setGrants] = useState<Grant[]>([]); const [events, setEvents] = useState<AuditEvent[]>([]); const [revision, setRevision] = useState(0); const [loading, setLoading] = useState(false); const [panel, setPanel] = useState('grants');
  const { busy, run } = useAction(operations);
  useEffect(() => {
    setGrants([]); setEvents([]); if (!selected) return;
    const abort = new AbortController(); setLoading(true);
    void Promise.all([api.get<{ grants: Grant[] }>(workPath(selected.id) + '/grants', { signal: abort.signal }), api.get<{ events: AuditEvent[] }>(workPath(selected.id) + '/audit', { signal: abort.signal })]).then(([grantResponse, auditResponse]) => {
      if (!abort.signal.aborted) { setGrants(grantResponse.data.grants); setEvents(auditResponse.data.events); }
    }).catch(error => { if (!abort.signal.aborted) operations.onFailure(error); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [selected?.id, revision, operations.onFailure]);
  const grantAccess = () => void run(async () => {
    if (!selected) return;
    const expiresAt = permanent ? null : Math.floor(new Date(expiry).getTime() / 1000);
    if (!permanent && (!expiresAt || !Number.isFinite(expiresAt) || expiresAt <= Date.now() / 1000)) throw new Error('请选择将来的授权截止时间。');
    await api.post(workPath(selected.id) + '/grants', { username: username.trim(), expires_at: expiresAt, can_download: canDownload });
    setUsername(''); setRevision(value => value + 1); void message.success('授权已保存');
  });
  const revoke = (grant: Grant) => void run(async () => {
    if (!selected) return;
    await api.post(workPath(selected.id) + '/grants/' + encodeURIComponent(grant.id) + '/revoke');
    setRevision(value => value + 1); void message.success('授权已撤销');
  });
  if (!selected) return <Card><Empty description="请先添加作品"><Button type="primary" onClick={onOpenLibrary}>前往作品管理</Button></Empty></Card>;
  const grantsPanel = <div className="copyright-stack"><form className="copyright-grant-form" onSubmit={event => { event.preventDefault(); if (username.trim()) grantAccess(); }}>
    <div className="copyright-field"><label htmlFor="copyright-grant-user">授权用户名</label><Input id="copyright-grant-user" aria-label="授权用户名" disabled={busy} maxLength={32} value={username} onChange={event => setUsername(event.target.value)} placeholder="填写已注册的用户名" /></div>
    <div className="copyright-field"><label htmlFor="copyright-expiry">授权截止时间</label><Input id="copyright-expiry" aria-label="授权截止时间" type="datetime-local" value={permanent ? '' : expiry} disabled={busy || permanent} onChange={event => setExpiry(event.target.value)} /><Checkbox checked={permanent} disabled={busy} onChange={event => setPermanent(event.target.checked)}>不设截止时间</Checkbox></div>
    <div className="copyright-grant-options"><Checkbox checked={canDownload} disabled={busy} onChange={event => setCanDownload(event.target.checked)}>允许下载原文件</Checkbox><Button type="primary" htmlType="submit" disabled={!username.trim()} loading={busy}>添加授权</Button></div>
  </form><Table<Grant> rowKey="id" loading={loading} dataSource={grants} scroll={{ x: 650 }} pagination={{ pageSize: 6 }} locale={{ emptyText: '暂无授权记录' }} columns={[
    { title: '用户', dataIndex: 'username' }, { title: '截止时间', dataIndex: 'expires_at', render: (value: number | null) => value === null ? '长期有效' : time(value) },
    { title: '权限', dataIndex: 'can_download', render: (value: boolean) => value ? '查看与下载' : '查看' },
    { title: '状态', render: (_: unknown, grant) => <Tag color={grant.revoked || (grant.expires_at !== null && grant.expires_at <= Date.now() / 1000) ? 'default' : 'success'}>{grant.revoked ? '已撤销' : grant.expires_at !== null && grant.expires_at <= Date.now() / 1000 ? '已到期' : '有效'}</Tag> },
    { title: '操作', render: (_: unknown, grant) => <Button size="small" danger disabled={busy || grant.revoked} onClick={() => revoke(grant)}>撤销授权</Button> },
  ]} /></div>;
  return <Card title="授权管理"><WorkSelect works={owned} value={selected.id} onChange={setSelectedId} disabled={busy} ownerOnly />
    <Tabs activeKey={panel} onChange={value => { if (!busy) setPanel(value); }} items={[{ key: 'grants', label: '访问授权', children: grantsPanel }, { key: 'audit', label: '访问记录', children: <Table<AuditEvent> rowKey="id" loading={loading} dataSource={events} pagination={{ pageSize: 10 }} scroll={{ x: 600 }} locale={{ emptyText: '暂无访问记录' }} columns={[{ title: '时间', dataIndex: 'created_at', render: time }, { title: '用户', dataIndex: 'username' }, { title: '操作', dataIndex: 'action', render: (action: string) => actionText[action] ?? action }]} /> }]} />
  </Card>;
}
export default function Copyright() {
  const [user, setUser] = useState<User | null>(); const [works, setWorks] = useState<Work[]>([]); const [worksLoading, setWorksLoading] = useState(false); const [revision, setRevision] = useState(0); const [active, setActive] = useState('works'); const [busy, setBusy] = useState(false); const [error, setError] = useState<string>();
  const onFailure = useCallback((failure: unknown) => {
    let text = errorMessage(failure);
    if (axios.isAxiosError(failure) && failure.response?.data instanceof Blob) {
      text = failure.response.status === 401 ? '登录已过期，请重新登录。' : failure.response.status === 403 ? '无访问权限，或授权已到期、撤销。' : '文件读取失败，请重试。';
    }
    setError(text);
    if (axios.isAxiosError(failure) && failure.response?.status === 401) { setUser(null); setWorks([]); }
    if (axios.isAxiosError(failure) && failure.response?.status === 403) setRevision(value => value + 1);
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    void api.get<{ user: User | null }>(prefix + '/auth/me', { signal: abort.signal }).then(({ data }) => { if (!abort.signal.aborted) setUser(data.user); }).catch(failure => { if (!abort.signal.aborted) { setUser(null); onFailure(failure); } });
    return () => abort.abort();
  }, [onFailure]);
  useEffect(() => {
    if (!user) { setWorks([]); setWorksLoading(false); return; }
    const abort = new AbortController(); setWorksLoading(true);
    void api.get<{ works: Work[] }>(prefix + '/works', { signal: abort.signal }).then(({ data }) => { if (!abort.signal.aborted) setWorks(data.works); }).catch(failure => { if (!abort.signal.aborted) onFailure(failure); }).finally(() => { if (!abort.signal.aborted) setWorksLoading(false); });
    return () => abort.abort();
  }, [user?.id, revision, onFailure]);
  useEffect(() => {
    if (!user) return;
    const refresh = () => setRevision(value => value + 1);
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, [user?.id]);
  const operations = { onFailure, onBusy: (value: boolean) => { setBusy(value); if (value) setError(undefined); } };
  const logout = async () => {
    if (busy) return; setBusy(true);
    try { await api.post(prefix + '/auth/logout'); setUser(null); setWorks([]); setError(undefined); setActive('works'); }
    catch (failure) { onFailure(failure); }
    finally { setBusy(false); }
  };
  const authenticated = (next: User) => { setError(undefined); setUser(next); setRevision(value => value + 1); };
  const refresh = () => { setError(undefined); setRevision(value => value + 1); };
  const guarded = (content: ReactNode) => user === undefined ? <Card loading /> : !user ? <AuthForm {...operations} onAuthenticated={authenticated} /> : content;
  return <div className="copyright-page"><header className="copyright-heading"><div><span className="copyright-eyebrow">应用场景</span><h1>数字版权保护</h1><p>管理作品、核验标识，按需授予访问权限。</p></div><div className="copyright-account">{user && <><Tag>{user.username}</Tag><Button onClick={() => void logout()} disabled={busy}>退出登录</Button></>}</div></header>
    {error && <Alert className="copyright-error" closable showIcon type="warning" message={error} onClose={() => setError(undefined)} />}
    <Tabs activeKey={active} destroyOnHidden onChange={value => { if (!busy) { setActive(value); setError(undefined); } }} items={[
      { key: 'works', label: '作品管理', children: guarded(<Library {...operations} works={works} loading={worksLoading} onRefresh={refresh} onCreated={() => refresh()} />) },
      { key: 'marks', label: '版权标识', children: guarded(<Marks {...operations} works={works} onOpenLibrary={() => setActive('works')} />) },
      { key: 'verify', label: '标识验证', children: <Verify {...operations} /> },
      { key: 'access', label: '授权管理', children: guarded(<Access {...operations} works={works} onOpenLibrary={() => setActive('works')} />) },
    ]} />
  </div>;
}
