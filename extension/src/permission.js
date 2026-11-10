/**
 * マイク許可ページ。
 * 拡張のページ（chrome-extension://<id>）で一度許可すれば、同じ拡張の offscreen document でもマイクを使える。
 */
const status = document.getElementById("status");

document.getElementById("grant").addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop()); // 許可を得るだけなので、すぐに解放する
    status.textContent = "✅ 許可されました。このタブを閉じて、Meet のタブで拡張アイコンを押してください。";
  } catch (e) {
    status.textContent = `❌ 許可されませんでした（${e.name}）。アドレスバー左のアイコンからマイクを許可してください。`;
  }
});

