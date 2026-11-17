const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({viewport:{width:960,height:540}});
  await p.goto('file://' + process.argv[2], {waitUntil:'networkidle'});
  await p.evaluate(() => document.fonts.ready);
  await p.pdf({path: process.argv[3], width:'960px', height:'540px', printBackground:true});
  await b.close();
})();
