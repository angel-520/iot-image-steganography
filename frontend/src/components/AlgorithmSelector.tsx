import { Select } from 'antd';
import type { Algorithm } from '../types';
interface Props { algorithms: Algorithm[]; value?: string; onChange: (value: string) => void; filter?: (algorithm: Algorithm) => boolean; disabled?: boolean; label?: string }
export function AlgorithmSelector({ algorithms, value, onChange, filter, disabled, label = '隐写算法' }: Props) {
  return <div className="algorithm-selector" data-testid="algorithm-select"><label className="field-label">{label}</label><Select value={value} onChange={onChange} disabled={disabled} className="full-width" aria-label={label}
    options={algorithms.filter(item => !filter || filter(item)).map(item => ({value:item.id,label:item.name,disabled:!item.available}))}/>
  </div>;
}
