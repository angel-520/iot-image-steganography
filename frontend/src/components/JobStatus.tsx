import { Alert, Progress } from 'antd';
import type { Task } from '../types';
export function JobStatus({task,loading,error}: {task?: Task; loading?: boolean; error?: string}) {
  return <>{error && <Alert type="error" showIcon message="操作未完成" description={error}/>} {loading && <div className="job-status" role="status"><strong>{task?.message?.replace(/真实|实际/g,'') ?? '正在处理…'}</strong><Progress percent={task?.progress ?? 0} status="active" size="small"/></div>}</>;
}
