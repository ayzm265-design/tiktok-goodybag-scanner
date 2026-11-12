# TikTok Goody Bag Scanner (starter)

## Keperluan
- Python 3.10+
- Sambungan internet
- LIVE sasaran mesti public/aktif dan boleh diakses oleh pustaka pihak ketiga.

## Jalankan di komputer atau VPS
```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```
Buka `http://127.0.0.1:8000` pada browser.

## Nota penting
Projek ini menggunakan TikTokLive, pustaka reverse-engineered pihak ketiga, bukan API rasmi TikTok. Ia boleh menerima komen/hadiah dan beberapa event apabila dihantar oleh LIVE. Tiada jaminan event Goody Bag atau syarat join disediakan melalui stream. Bahagian "Calon teks Goody Bag" hanya menandakan teks yang kelihatan dalam komen; ia tidak boleh membaca panel Goody Bag yang tidak didedahkan oleh event. Jangan masukkan kata laluan atau cookie TikTok ke aplikasi ini.
