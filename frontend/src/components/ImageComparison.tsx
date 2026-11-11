import { ImageViewer } from './ImageViewer';
export function ImageComparison({original,stego}: {original?: string; stego?: string}) {
  return <div className="image-comparison"><ImageViewer src={original} title="原始载体"/><ImageViewer src={stego} title="隐写结果"/></div>;
}
