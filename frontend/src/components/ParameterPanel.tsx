import { Input, InputNumber, Select, Switch } from 'antd';
import type { Algorithm, AlgorithmParams } from '../types';
export { defaultParams } from '../services/parameters';
interface Props { algorithm?: Algorithm; value: AlgorithmParams; onChange: (value: AlgorithmParams) => void; advanced?: boolean; disabled?: boolean }
export function ParameterPanel({ algorithm, value, onChange, advanced = true, disabled }: Props) {
  if (!algorithm) return null;
  const parameters = algorithm.parameters.filter(p => advanced || p.required || p.name === 'key');
  return <div className="parameter-panel">
    {parameters.map(parameter => {
      const current = value[parameter.name] ?? parameter.default;
      const change = (next: string | number | boolean) => onChange({ ...value, [parameter.name]: next });
      return <div className="parameter-field" key={parameter.name}><label className="field-label">{parameter.label}{parameter.required && <span className="required"> *</span>}</label>
        {parameter.options ? <Select className="full-width" aria-label={parameter.label} value={current as string | number} disabled={disabled} onChange={change} options={parameter.options.map(option => typeof option === 'object' ? option : ({label:String(option),value:option}))}/>
          : parameter.type === 'number' || parameter.type === 'integer' ? <InputNumber className="full-width" aria-label={parameter.label} value={typeof current === 'number' ? current : undefined} min={parameter.min} max={parameter.max} step={parameter.step ?? (parameter.type === 'integer' ? 1 : undefined)} disabled={disabled} onChange={next => { if (next !== null) change(next); }}/>
          : parameter.type === 'boolean' ? <Switch checked={Boolean(current)} disabled={disabled} onChange={change}/>
          : parameter.name === 'key' || parameter.type === 'password' ? <Input.Password aria-label={parameter.label} autoComplete="new-password" value={String(current ?? '')} disabled={disabled} placeholder="输入已知的散布密钥" onChange={event => change(event.target.value)}/>
          : <Input aria-label={parameter.label} value={String(current ?? '')} disabled={disabled} onChange={event => change(event.target.value)}/>}
        {parameter.description && <small>{parameter.description}</small>}
      </div>;
    })}
  </div>;
}
