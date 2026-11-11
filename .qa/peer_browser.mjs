import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import path from 'node:path';
import fs from 'node:fs/promises';
const browser=await chromium.launch({channel:'msedge',headless:true});
try {
  const page=await browser.newPage({viewport:{width:1440,height:1080}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8017/network-lab');
  await page.getByRole('tab',{name:'双机 / 公网',exact:true}).click();
  await page.getByText(/127\.0\.0\.1:\d+/).waitFor();
  await page.getByRole('textbox',{name:'远程秘密文字',exact:true}).fill('浏览器双端交付 🔐');
  await page.getByRole('button',{name:/发送给对方/}).click();
  await page.getByText('对方已接收',{exact:true}).waitFor();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.waitForTimeout(350);
  await page.screenshot({path:'.qa/peer-desktop.png',fullPage:true});
  const files=await fs.readdir('.qa/peer-inbox');
  let match=false;
  for(const file of files.filter(name=>name.endsWith('.txt'))) {
    if(await fs.readFile(path.join('.qa/peer-inbox',file),'utf8')==='浏览器双端交付 🔐') match=true;
  }
  if(!match)throw Error('Receiver did not save the exact text');
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'.qa/peer-mobile.png',fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Mobile page overflows');
  if(errors.length)throw Error(errors.join('; '));
  console.log(JSON.stringify({authenticatedBrowserTransfer:true,receiverFileExact:true,mobileNoOverflow:true,pageErrors:errors}));
} finally {await browser.close();}
