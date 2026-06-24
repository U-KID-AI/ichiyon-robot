# デッキ検索の画像処理安全制御

デッキ検索はX検索件数を保ったまま、画像処理でBot本体が巻き込まれないように制御する。

- 通常検索は `x_search_max_results=50`、`image_scan_limit=30`、`image_scan_concurrency=2`、`stop_after_candidates=true` を維持。
- 高精度検索は `high_accuracy_x_search_max_results=100`、`high_accuracy_image_scan_limit=100`、`high_accuracy_image_scan_concurrency=2`、`high_accuracy_stop_after_candidates=false` を維持。
- デバッグ用tweet指定は廃止済み。通常経路へ戻さない。
- 画像取得前に `Content-Type` を確認し、JPEG/PNG/WebP以外はスキップ。
- `Content-Length` と実ダウンロード量は3MBを上限にする。
- HTTPは接続2秒、読み込み3秒、1画像5秒以内を目安に遅いURLを捨てる。
- HEADは1秒で諦め、GET側の判定へ進む。
- QR判定前に画像の長辺を1200pxへ縮小する。
- QR判定も個別タイムアウトで保護する。
- 通常検索は全体30秒を目安に安全中断する。
- 同じ画像URLのQR判定結果は短時間キャッシュし、成功/失敗どちらも再処理を避ける。
- ログには `search_count`、`image_url_count`、`scanned_image_count`、`skipped_by_content_type`、`skipped_by_content_length`、`skip_image_timeout`、`skip_head_timeout`、`skip_download_timeout`、`skip_decode_timeout`、`cancelled_image_tasks`、`qr_detected_count`、`elapsed_seconds`、`timeout` を出す。
