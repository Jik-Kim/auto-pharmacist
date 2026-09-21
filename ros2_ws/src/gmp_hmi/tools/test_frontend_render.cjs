/* 실제 Chromium에서 템플릿 로딩·상태 갱신·잔량 카드 제거 회귀를 검사한다.
 * 실행: node tools/test_frontend_render.cjs (playwright 및 Chromium 필요)
 * 별도 설치 경로는 NODE_PATH / HMI_TEST_CHROMIUM으로 지정한다. ROS/로봇 호출 없음.
 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({headless:true,
  ...(process.env.HMI_TEST_CHROMIUM?{executablePath:process.env.HMI_TEST_CHROMIUM}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://hmi.test/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:
    fs.readFileSync(path.join(root,'templates/index.html'),'utf8')
     .replace('{{ hmi_config | tojson }}',JSON.stringify({demo:true}))});
   const assets={'/static/hmi.js':'application/javascript','/static/data-source.js':'application/javascript',
    '/static/hmi.css':'text/css','/static/safety-recovery.css':'text/css'};
   if(assets[url.pathname])return route.fulfill({contentType:assets[url.pathname],
    body:fs.readFileSync(path.join(root,url.pathname.slice(1)))});
   return route.fulfill({status:404,body:''});
  });
  await page.goto('http://hmi.test/');
  await page.waitForFunction(()=>document.querySelector('#recipe').options.length===3);
  await page.waitForFunction(()=>document.querySelector('#history table'));
  const scenarios=['idle','running','qa','refill','low_grams','height_low','contact','error','offline','empty'];
  for(const scenario of scenarios){
   await page.evaluate(async scenario=>{
    source.reset(scenario);
    render(await source.status());
    // 주기 갱신 외 직접 호출도 검사하여 tick의 예외 처리에 가려진 오류를 잡는다.
    render(await source.status());
   },scenario);
  }
  await page.evaluate(async()=>{
   source.reset('idle');
   const status=await source.status();
   // 운영/시험 namespace의 표시 분기도 같은 템플릿으로 검사한다. DDS 시험은 아니다.
   source.demo=false;
   try{for(const namespace of ['/cell','/hmi_test']){
    status.diagnostics={...status.diagnostics,namespace};render(status);
   }}finally{source.demo=true;}
   if($('stockAlert').open)$('stockAlert').close();
   render(await source.status());
  });
  await page.waitForFunction(()=>document.querySelector('#connection').textContent.startsWith('상태 수신'));
  assert.equal(await page.locator('#operation > .column').count(),4);
  assert.equal(await page.locator('#operation > .column').nth(1).locator('#results').count(),1);
  assert.equal(await page.locator('#legacyInventory').isVisible(),false);
  assert.equal(await page.locator('#openCollectionConfirm').count(),0);
  assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#operation > .column')]
   .some(c=>[...c.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()==='null'))),false);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('PASS: initial load, 10 demo states, live/test render branches, hidden inventory, 4 columns, mobile overflow, no JS errors');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
