import assert from 'node:assert/strict';
import path from 'node:path';
import { chromium } from '../frontend/node_modules/playwright/index.mjs';

const origin = process.env.ISTG_BASE_URL ?? 'http://127.0.0.1:8017';
const browser = await chromium.launch({channel:'msedge', headless:true});
const page = await browser.newPage({viewport:{width:1440,height:900}});
const errors=[];
page.on('pageerror', error=>errors.push(error.message));
const visible = locator=>locator.waitFor({state:'visible'});
const scenes=[['copyright','数字版权与水印验证'],['medical','医疗影像隐私保护'],['robustness','鲁棒性传输测试']];
async function shot(name) { await page.evaluate(()=>window.scrollTo(0,0)); await page.waitForTimeout(750); await page.screenshot({path:path.resolve('.qa/scene-'+name+'.png'),fullPage:true}); }
async function overflow(name) { const geometry=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth})); assert(geometry.scroll<=geometry.width+2, name+' overflow '+JSON.stringify(geometry)); }
try {
  for(const width of process.env.ISTG_SKIP_LAYOUT ? [] : [1440,1280,768,390]) {
    await page.setViewportSize({width,height:900});
    for(const [scene,title] of scenes) {
      await page.goto(origin+'/applications/'+scene); await visible(page.getByRole('heading',{name:title,exact:true}));
      await page.waitForTimeout(250); await overflow(scene+' '+width); await shot(width+'-'+scene);
      await page.getByText(/^高级设置/).click(); await overflow(scene+' settings '+width); await shot(width+'-'+scene+'-settings');
    }
  }
  console.log('12 scene layouts and expanded settings verified');
  await page.setViewportSize({width:1440,height:900});
  await page.goto(origin);
  const groups=await page.locator('.desktop-navigation > button').allTextContents();
  assert.deepEqual(groups.map(text=>text.trim()),['隐写实验','分析评估','通信实验','应用场景']);
  for(const [scene,title] of scenes) {
    await page.getByRole('button',{name:'应用场景',exact:true}).hover();
    await page.getByRole('menuitem',{name:title,exact:true}).click();
    await page.waitForURL(origin+'/applications/'+scene); await visible(page.getByRole('heading',{name:title,exact:true}));
    assert(await page.getByRole('button',{name:'应用场景',exact:true}).evaluate(el=>el.classList.contains('active')));
  }
  const trigger=page.getByRole('button',{name:'应用场景',exact:true});
  await page.mouse.move(0,300); await trigger.focus(); await trigger.press('ArrowDown');
  await visible(page.getByRole('menuitem',{name:scenes[0][1],exact:true})); await page.waitForTimeout(150);
  assert(await page.evaluate(()=>!!document.activeElement.closest('[role="menu"]')));
  await page.keyboard.press('Escape');
  await page.getByRole('menu').waitFor({state:'hidden'});
  for(const width of [1280,1100,1024]) {
    await page.setViewportSize({width,height:900});
    await page.waitForTimeout(300);
    const rects=await page.locator('.site-header-inner > *').evaluateAll(els=>els.map(el=>({left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right})));
    assert(rects[0].right<=rects[1].left && rects[1].right<=rects[2].left, 'Header controls overlap '+width); await overflow('header '+width);
  }
  await page.setViewportSize({width:390,height:844});
  for(const [scene,title] of scenes) {
    await page.getByRole('button',{name:'打开导航',exact:true}).click();
    await page.getByRole('navigation',{name:'移动导航'}).getByRole('link',{name:title,exact:true}).click();
    await page.waitForURL(origin+'/applications/'+scene); await page.getByRole('dialog').waitFor({state:'hidden'});
  }
  await page.setViewportSize({width:1440,height:900}); await page.goto(origin);
  for(const [id,scene] of [['medical-imaging','medical'],['robust-transmission','robustness'],['copyright-protection','copyright']]) {
    assert.equal(await page.locator('#'+id).getByRole('link',{name:'进入场景',exact:true}).getAttribute('href'),'/applications/'+scene);
  }
  await visible(page.getByRole('heading',{name:'探索你的图像隐写场景',exact:true}));
  console.log('Desktop, keyboard, mobile navigation and homepage scene entrances verified');
  const cover=await page.evaluate(()=>{ const canvas=document.createElement('canvas');canvas.width=canvas.height=256;const ctx=canvas.getContext('2d');const image=ctx.createImageData(256,256);let seed=47;for(let i=0;i<image.data.length;i++){seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;image.data[i]=i%4===3?255:seed&255;}ctx.putImageData(image,0,0);return canvas.toDataURL('image/png').split(',')[1]; });
  const upload=async role=>{await page.getByTestId(role+'-upload').locator('input[type=file]').setInputFiles({name:'scene-cover.png',mimeType:'image/png',buffer:Buffer.from(cover,'base64')});};
  const select=async (label,option)=>{await page.locator('.ant-select').filter({has:page.getByRole('combobox',{name:label,exact:true})}).locator('.ant-select-selector').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(option,{exact:true}).click();};
  async function write(scene,{encrypted=false,scatter=false}={}) {
    await page.goto(origin+'/applications/'+scene); await upload('cover');
    if(scene==='copyright') {await page.getByRole('textbox',{name:'作者或机构',exact:true}).fill('ISTG 测试作者'); await page.getByRole('textbox',{name:'作品名称',exact:true}).fill('湖岸与远山');await page.getByRole('textbox',{name:'权利声明',exact:true}).fill('版权所有，未经许可不得转载。');await page.getByLabel('创建日期',{exact:true}).fill('2026-11-11');}
    else {await page.getByRole('textbox',{name:'检查说明',exact:true}).fill('影像说明测试：仅作图像隐写演示。'); await page.getByRole('textbox',{name:'影像编号',exact:true}).fill('DEMO-1101');}
    if(encrypted||scatter) {await page.getByText(/^高级设置/).click();}
    if(scatter) {await select('隐写算法','Random / Scatter LSB');await page.getByRole('textbox',{name:'散布位置密钥',exact:true}).fill('scene-position-secret-47');await page.getByRole('spinbutton',{name:'每通道低位数',exact:true}).fill('2');}
    if(encrypted) {await select('加密方式','AES-256-GCM'); await page.getByRole('textbox',{name:'加密密码',exact:true}).fill('scene-encryption-secret-47');await page.getByRole('textbox',{name:'确认密码',exact:true}).fill('scene-encryption-secret-47');}
    await page.waitForFunction(()=>!document.querySelector('[data-testid="scene-embed-submit"]').disabled);
    const request=page.waitForRequest(req=>req.url().endsWith('/api/embed')&&req.method()==='POST');
    await page.getByTestId('scene-embed-submit').click(); const data=(await request).postDataJSON();
    await visible(page.getByText('写入后恢复检查通过',{exact:true}));
    const state=await page.evaluate(()=>({state:history.state.usr,url:location.href,local:JSON.stringify(localStorage),session:JSON.stringify(sessionStorage)}));
    assert.deepEqual(Object.keys(state.state),['experimentId']); assert(!JSON.stringify(state).includes('scene-encryption-secret')); assert(!JSON.stringify(state).includes('scene-position-secret')); assert(!JSON.stringify(state).includes(data.text));
    if(encrypted) assert.equal(await page.getByRole('textbox',{name:'验证解密密码',exact:true}).inputValue(),'');
    if(scatter) assert.equal(await page.getByRole('textbox',{name:'验证散布位置密钥',exact:true}).inputValue(),'');
    const fillCredentials=async()=>{if(encrypted) await page.getByRole('textbox',{name:'验证解密密码',exact:true}).fill('scene-encryption-secret-47');if(scatter) await page.getByRole('textbox',{name:'验证散布位置密钥',exact:true}).fill('scene-position-secret-47');};
    await fillCredentials(); await page.getByTestId('scene-verify').click();await visible(page.getByTestId('scene-verification-result'));await visible(page.getByText('与写入内容一致',{exact:true}));
    assert.equal(await page.getByRole('textbox',{name:scene==='copyright'?'提取出的版权信息':'恢复的影像说明',exact:true}).inputValue(),data.text);
    if(encrypted) await visible(page.getByText('认证解密通过',{exact:true}));
    for(const label of [scene==='copyright'?'下载水印图片':'下载隐写图片','导出报告','下载文本']) {const href=await page.getByRole('link',{name:new RegExp(label)}).getAttribute('href');const response=await page.request.get(new URL(href,origin).href);assert(response.ok(),label+' failed');assert((await response.body()).length>0);}
    await shot(scene+(scatter?'-scatter':encrypted?'-encrypted':'')+'-result');
    for(const width of [1280,768,390]) {await page.setViewportSize({width,height:900});await overflow('result '+scene+width);await shot(width+'-'+scene+'-result');}
    await page.setViewportSize({width:1440,height:900});
    return {data,fillCredentials};
  }
  await write('copyright');
  await page.getByRole('button',{name:'信息提取',exact:true}).click();await page.waitForURL(origin+'/extract');await page.getByTestId('extract-submit').waitFor({state:'visible'});await page.waitForFunction(()=>!(document.querySelector('[data-testid="extract-submit"]') instanceof HTMLButtonElement) || !(document.querySelector('[data-testid="extract-submit"]')).disabled);assert(await page.getByTestId('extract-submit').isEnabled());
  await page.getByTestId('extract-submit').click();await visible(page.getByRole('textbox',{name:'恢复的秘密文本',exact:true}));
  await page.getByRole('button',{name:'返回实验结果',exact:true}).click();await page.waitForURL(origin+'/applications/copyright');await visible(page.getByText('写入后恢复检查通过',{exact:true}));
  await page.getByRole('button',{name:'图像质量分析',exact:true}).click();await page.waitForURL(origin+'/analysis?tab=performance&panel=quality');await page.getByRole('button',{name:'计算图像质量',exact:true}).click();await visible(page.getByText('PSNR',{exact:true}));await page.getByRole('button',{name:'返回实验结果',exact:true}).click();await page.waitForURL(origin+'/applications/copyright');
  await page.getByRole('button',{name:'传输测试',exact:true}).click();await page.waitForURL(origin+'/applications/robustness');await visible(page.getByRole('button',{name:'更换图片',exact:true}));
  await page.getByText(/^高级设置/).click();await page.getByRole('spinbutton',{name:'JPEG 质量',exact:true}).fill('100');await page.getByRole('spinbutton',{name:'噪声强度',exact:true}).fill('0');await page.getByRole('spinbutton',{name:'裁剪保留宽高比例',exact:true}).fill('1');await page.getByRole('spinbutton',{name:'尺寸缩放比例',exact:true}).fill('1');
  await page.getByTestId('scene-robustness-submit').click();await visible(page.getByTestId('scene-robustness-results'));assert(await page.getByTestId('scene-robustness-results').locator('tbody tr').count()>=4);assert(await page.getByTestId('scene-robustness-results').getByText('已恢复',{exact:true}).count()>=3);
  for(const width of [1440,1280,768,390]) {await page.setViewportSize({width,height:900});await overflow('robustness results '+width);await shot(width+'-robustness-result');}
  await page.getByRole('checkbox',{name:'图片压缩',exact:true}).uncheck();assert.equal(await page.getByTestId('scene-robustness-results').count(),0);
  await page.setViewportSize({width:1440,height:900});await write('medical',{encrypted:true});
  await page.getByRole('button',{name:'修改信息',exact:true}).click();assert.equal(await page.getByRole('textbox',{name:'检查说明',exact:true}).inputValue(),'');await visible(page.getByRole('button',{name:'更换图片',exact:true}));await page.getByText(/^高级设置/).click();assert.equal(await page.getByRole('textbox',{name:'加密密码',exact:true}).inputValue(),'');
  await write('copyright',{encrypted:true,scatter:true});
  await page.getByRole('button',{name:'信息提取',exact:true}).click();await page.waitForURL(origin+'/extract');await visible(page.getByRole('textbox',{name:'散布位置密钥',exact:true}));assert.equal(await page.getByRole('textbox',{name:'散布位置密钥',exact:true}).inputValue(),'');assert.equal(await page.getByRole('spinbutton',{name:'每通道低位数',exact:true}).inputValue(),'2');
  await page.getByRole('button',{name:'返回实验结果',exact:true}).click();await page.waitForURL(origin+'/applications/copyright');
  await page.reload();await visible(page.getByText('实验暂存已失效，请重新上传图片',{exact:true}));assert.equal(await page.getByText('写入后恢复检查通过',{exact:true}).count(),0);await page.reload();assert.equal(await page.getByText('实验暂存已失效，请重新上传图片',{exact:true}).count(),0);
  await write('copyright');await page.route('**/api/extract',route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:{code:'file_not_found',message:'文件已过期，请重新上传'}})}));await page.getByTestId('scene-verify').click();await visible(page.getByText('实验暂存已失效，请重新上传图片',{exact:true}));assert.equal(await page.getByText('写入后恢复检查通过',{exact:true}).count(),0);await page.unroute('**/api/extract');
  console.log('Copyright, encrypted medical, encrypted scatter round trips, downloads, reports, handoffs, robustness matrix, refresh and expired-file handling verified');
  assert.deepEqual(errors,[]);
} finally {await browser.close();}
