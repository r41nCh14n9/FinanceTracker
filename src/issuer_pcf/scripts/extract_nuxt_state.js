// 把元大投信 PCF 頁面尾端的 window.__NUXT__=(function(...){...})(...) 這段運算式
// 丟進一個乾淨的沙箱執行一次，印出解析後的物件（JSON），不牽涉任何 DOM 或網路。
//
// 用法：node extract_nuxt_state.js <html檔案路徑>
// 成功：把結果印到 stdout，exit code 0。
// 失敗：把一行說明印到 stderr，exit code 非 0（找不到 window.__NUXT__、
//       運算式執行出錯、或結果無法序列化成 JSON 都算失敗）。
//
// 只用 Node 內建的 fs / vm 模組，不需要裝任何套件；語法也刻意寫得舊一點
// （不用 optional chaining、nullish coalescing），避免舊版 Node 直接語法錯誤。
var fs = require('fs');
var vm = require('vm');

function fail(message) {
  process.stderr.write(message + '\n');
  process.exit(1);
}

var htmlPath = process.argv[2];
if (!htmlPath) {
  fail('缺少 HTML 檔案路徑參數');
}

var html;
try {
  html = fs.readFileSync(htmlPath, 'utf8');
} catch (readErr) {
  fail('讀取 HTML 檔案失敗：' + readErr.message);
}

var marker = 'window.__NUXT__=';
var markerIdx = html.indexOf(marker);
if (markerIdx === -1) {
  fail('找不到 window.__NUXT__ 這段標記，頁面可能已改版');
}

var exprStart = markerIdx + marker.length;
var exprEnd = html.indexOf('</script>', exprStart);
if (exprEnd === -1) {
  fail('找不到 __NUXT__ 運算式結尾的 </script>，頁面可能已改版');
}

var expr = html.slice(exprStart, exprEnd).trim();
if (expr.charAt(expr.length - 1) === ';') {
  expr = expr.slice(0, -1);
}

var result;
try {
  result = vm.runInNewContext(expr, {});
} catch (evalErr) {
  fail('執行 __NUXT__ 運算式失敗：' + evalErr.message);
}

var output;
try {
  output = JSON.stringify(result);
} catch (jsonErr) {
  fail('__NUXT__ 解析結果無法序列化為 JSON：' + jsonErr.message);
}

process.stdout.write(output);
