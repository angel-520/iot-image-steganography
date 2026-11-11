import { Empty, Image } from 'antd';
export function ImageViewer({src,title}: {src?: string; title?: string}) {
  return <div className="image-viewer">{title && <div className="viewer-title">{title}</div>}{src ? <Image src={src} alt={title ?? '图像预览'} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="上传图片后在此预览"/>}</div>;
}
