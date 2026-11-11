import { Button, Select, Tooltip } from 'antd';
import { QuestionCircleOutlined } from '@ant-design/icons';
import type { Algorithm } from '../types';
interface Props { algorithms: Algorithm[]; value?: string; onChange: (value: string) => void; filter?: (algorithm: Algorithm) => boolean; disabled?: boolean; label?: string }
export function AlgorithmSelector({ algorithms, value, onChange, filter, disabled, label = '隐写算法' }: Props) {
  const selected = algorithms.find(item => item.id === value);
  return <div className="algorithm-selector" data-testid="algorithm-select"><div className="field-label field-label--help"><span>{label}</span>{selected && <Tooltip title={<>{selected.description}<br/>{selected.notes}</>}><Button type="text" size="small" aria-label="算法说明" icon={<QuestionCircleOutlined/>}/></Tooltip>}</div><Select value={value} onChange={onChange} disabled={disabled} className="full-width" aria-label={label}
    options={algorithms.filter(item => !filter || filter(item)).map(item => ({value:item.id,label:item.name,disabled:!item.available}))}/>
  </div>;
}
