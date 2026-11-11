import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import 'antd/dist/reset.css';
import './styles/index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{
      token: {
        colorPrimary: '#0d8f87', colorInfo: '#0d8f87', colorSuccess: '#0d9b82',
        borderRadius: 12, colorText: '#20354a', colorTextSecondary: '#6e8190',
        colorBorder: '#dce4eb', fontFamily: 'Georgia, "Times New Roman", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif',
        fontSize: 15, fontSizeSM: 13, fontSizeLG: 17, lineHeight: 1.65,
        controlHeight: 44, controlHeightSM: 32, controlHeightLG: 48,
        motionDurationFast: '0.15s', motionDurationMid: '0.24s', motionDurationSlow: '0.36s',
      },
      components: { Card: {headerFontSize:17,headerHeight:60}, Button: {fontWeight:500}, Layout: {bodyBg:'#f5f7fa',headerBg:'#ffffff'}, Menu: {itemBorderRadius:9,itemHeight:48}, Table: {fontSize:14}, Tabs: {titleFontSize:15} },
    }}><App/></ConfigProvider>
  </React.StrictMode>,
);
