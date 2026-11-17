<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>示波器截图归档</title>
<style>
body{margin:32px auto;padding:0 24px;max-width:1440px;background:#f4f6fa;color:#172033;font-family:system-ui,"Microsoft YaHei",sans-serif}
h1{font-size:28px}p{color:#526079;overflow-wrap:anywhere}a{color:#165dba;text-decoration:none}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:24px;margin:24px 0}
article{padding:16px;border-radius:12px;background:white;box-shadow:0 2px 10px #0001}img{display:block;width:100%;border-radius:6px;background:#000}
h2{font-size:18px;margin-bottom:8px}article p{margin:6px 0}
</style>
</head>
<body>
<h1>示波器截图归档</h1>
<p>共 ${total} 张 · 时间为北京时间（UTC+8）· 最近截图在前 · <a href="index.csv">CSV 索引</a></p>
${empty_notice}
<main>${cards}</main>
</body>
</html>
