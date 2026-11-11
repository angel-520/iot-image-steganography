import { Input, Select } from 'antd';
import { LockOutlined } from '@ant-design/icons';
import type { Encryption } from '../types';
interface Props { value: Encryption; onChange: (value: Encryption) => void; password: string; onPasswordChange: (value: string) => void; confirm: string; onConfirmChange: (value: string) => void; disabled?: boolean }
export function EncryptionSettings({value,onChange,password,onPasswordChange,confirm,onConfirmChange,disabled}: Props) {
  return <div className="encryption-settings"><label className="field-label"><LockOutlined /> 加密方式</label>
    <div data-testid="encryption-select"><Select className="full-width" aria-label="加密方式" value={value} onChange={onChange} disabled={disabled} options={[{value:'none',label:'不加密'},{value:'AES-256-GCM',label:'AES-256-GCM'},{value:'ChaCha20-Poly1305',label:'ChaCha20-Poly1305'}]}/></div>
    {value !== 'none' && <div className="password-grid"><div><label className="field-label">加密密码</label><Input.Password data-testid="password" aria-label="加密密码" value={password} onChange={e=>onPasswordChange(e.target.value)} disabled={disabled} autoComplete="new-password" placeholder="设置加密密码"/></div><div><label className="field-label">确认密码</label><Input.Password data-testid="confirm-password" aria-label="确认密码" value={confirm} onChange={e=>onConfirmChange(e.target.value)} disabled={disabled} autoComplete="new-password" placeholder="再次输入密码" status={confirm && password!==confirm ? 'error' : undefined}/></div></div>}
  </div>;
}
