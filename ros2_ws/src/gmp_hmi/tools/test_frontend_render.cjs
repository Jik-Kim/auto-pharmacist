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
  // 원료별 분주 결과: 지금 배치가 레시피 순서 A→B→C 로 맨 위(결과 없는 원료도 자리 — QA 대기 = A·B 결과, C 대기),
  // 이전 배치는 구분 줄 아래. 이전 배치 결과를 하나 섞어 넣어 순서를 본다.
  // 지금 배치 A·B·C 가 표 칸을 꽉 채우고(마지막 줄이 칸 바닥), 이전 배치는 스크롤 아래에 있어야 한다.
  // 렌더와 측정을 한 evaluate 안에서 한다 — 사이에 데모 주기 갱신이 끼면 섞어 넣은 이전 배치가 사라진다.
  const resultTable=await page.evaluate(async()=>{source.reset('qa');const st=await source.status();
   st.results=[{batch_id:'B-000',material_id:'A',target_g:69,actual_g:69,error_pct:0,verdict:'OK',attempts:1},...st.results];render(st);
   const box=$('results').getBoundingClientRect(),rows=[...document.querySelectorAll('#results tbody tr')];
   return {rows:rows.map(tr=>tr.classList.contains('sep')?'|'+tr.textContent:tr.cells[0].textContent),
    fills:Math.abs(rows[2].getBoundingClientRect().bottom-box.bottom)<=3&&rows[3].getBoundingClientRect().top>=box.bottom-3,
    tight:document.querySelector('section:has(#results)').className.includes('tight')};});
  assert.deepEqual(resultTable,{rows:['A','B','C','|이전 배치 B-000','A'],fills:true,tight:false},JSON.stringify(resultTable));
  // 첫 배치에 원료 A 잔량 경고 + 「부족 알림 확인」 버튼이 떠 결과 칸이 줄어도 A·B·C 가 칸 안에 보여야 한다(9/29 교육장 PC).
  const tight=await page.evaluate(async()=>{source.reset('running');const st=await source.status();st.results=[];
   Object.assign(st.inventory.items[0],{remaining_g:138,percent:13.8,low:true,available_g:138});render(st);
   $('showStockAlert').hidden=false;fitCurrentResults();
   const box=$('results').getBoundingClientRect(),rows=[...document.querySelectorAll('#results tbody tr')];
   return {warning:!$('stockWarning').hidden,rows:rows.map(tr=>tr.cells[0].textContent),inside:rows[2].getBoundingClientRect().bottom<=box.bottom+1};});
  assert.deepEqual(tight,{warning:true,rows:['A','B','C'],inside:true},JSON.stringify(tight));
  await page.evaluate(async()=>{source.reset('idle');render(await source.status());});
  await page.waitForFunction(()=>document.querySelector('#connection').textContent.startsWith('상태 수신'));
  assert.equal(await page.locator('#operation > .column').count(),4);
  assert.equal(await page.locator('#operation > .column').nth(1).locator('#results').count(),1);
  assert.equal(await page.locator('.inventory-card').isVisible(),true);
  assert.equal(await page.locator('#openCollectionConfirm').count(),0);
  assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#operation > .column')]
   .some(c=>[...c.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()==='null'))),false);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('PASS: initial load, 10 demo states, results current batch A→B→C fills box, older below scroll, fits under stock warning, live/test render branches, visible inventory card, 4 columns, mobile overflow, no JS errors');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
