# KẾ HOẠCH XÂY DỰNG PIPELINE TẢI MEDIA TỰ ĐỘNG BẰNG PYTHON (ANTI-BAN)

## 1. Tổng Quan Mục Tiêu
Xây dựng một hệ thống thu thập và tải tư liệu media toàn diện (bao gồm Video, Audio, Thumbnail, Subtitle) hoạt động ổn định ở quy mô lớn. Kế hoạch tập trung vào việc áp dụng các kỹ thuật chống chặn (Anti-Ban / Anti-Bot) từ mức độ cơ bản đến nâng cao để đảm bảo luồng cào dữ liệu không bị gián đoạn do dính rate-limit hoặc khóa IP.

## 2. Kiến Trúc Công Cụ Cốt Lõi (Core Engine)
Thay vì sử dụng các công cụ tự động hóa trình duyệt nặng nề và dễ để lại dấu vết như Selenium/Playwright (đòi hỏi xử lý Driver ID rất phức tạp), hệ thống sẽ sử dụng **`yt-dlp`** qua Python API làm nhân xử lý cốt lõi.
* **Ưu điểm:** Tốc độ cao, tối ưu băng thông, hỗ trợ bóc tách trực tiếp luồng stream của hơn 1000+ trang web video, tích hợp sẵn cơ chế trích xuất subtitle và thumbnail chất lượng cao.

---

## 3. Các Phân Hệ & Chiến Lược Chống Chặn (Anti-Ban Strategies)

### Phân hệ A: Tối ưu hóa cấu hình tải dữ liệu
* **Tải trọn gói một lần:** Sử dụng `ydl_opts` để cấu hình tải đồng thời video/audio, thumbnail, và tệp sub (`.vtt`, `.srt`) theo mã ngôn ngữ chỉ định (`vi`, `en`), tránh việc phải request nhiều lần cho một URL.
* **Xóa bộ nhớ đệm liên tục:** Thiết lập `'rm_cachedir': True` để loại bỏ các tệp session và cookie cũ còn sót lại sau mỗi phiên, giảm thiểu việc bị hệ thống quét chuỗi hành vi bất thường.

### Phân hệ B: Kỹ thuật mô phỏng hành vi con người (Behavioral Obfuscation)
* **Khoảng nghỉ ngẫu nhiên (Random Delay):** Không sử dụng thời gian chờ cố định. Hệ thống sẽ tính toán khoảng nghỉ ngẫu nhiên bằng hàm `random.uniform(min, max)` (ví dụ từ 5.5 đến 15 giây) giữa các lượt tải để phá vỡ pattern nhận diện của bot.
* **Giới hạn tốc độ (Rate Limiting):** Tải với băng thông tối đa của server sẽ kích hoạt cảnh báo bất thường. Thiết lập giới hạn tốc độ tải (ví dụ: tối đa 5MB/s qua tham số `'ratelimit': 5000000`) giúp dòng lưu lượng trông tự nhiên như một người dùng đang xem video trực tuyến.

### Phân hệ C: Xoay vòng định danh thiết bị (Identity Rotation)
* **Xoay vòng User-Agent giả lập:** Tích hợp thư viện `fake-useragent` để tự động sinh ra các chuỗi User-Agent ngẫu nhiên của các trình duyệt phổ biến (Chrome, Safari, Edge, Firefox) trên các hệ điều hành khác nhau (Windows, MacOS, Linux) cho mỗi request.
* **Quản lý Cookies thông minh:** Chỉ sử dụng Cookies khi thực sự cần thiết (tải video ẩn tư nhân hoặc giới hạn độ tuổi). Sử dụng các tài khoản phụ (burner accounts) thay vì tài khoản chính. Thường xuyên xóa hoặc reset session cookie để tránh bị xâu chuỗi hành vi phá hoại.

### Phân hệ D: Quản trị hạ tầng mạng & Thay đổi IP (Network-Level Anti-Ban)
Khi số lượng tải lên tới hàng trăm/hàng nghìn video, thay đổi phần mềm là chưa đủ, cần phải thay đổi IP:
* **Xoay vòng Proxy dân cư (Residential Proxies):** Tích hợp danh sách proxy vào tham số `'proxy'`. Nên ưu tiên proxy dân cư thay vì proxy Datacenter (như AWS, Google Cloud) vì các dải IP của Datacenter thường nằm sẵn trong blacklist của các nền tảng lớn.
* **Tự động đổi IP bằng VPN CLI:** Nếu chạy trên máy local hoặc VPS riêng, viết một script phụ để gọi Command Line Interface (CLI) của các dịch vụ VPN (như NordVPN, ExpressVPN hoặc CyberGhost) để thực hiện lệnh đổi server/quốc gia sau khi hoàn thành một số lượng video nhất định (ví dụ: cứ 30-50 video đổi IP một lần).

### Phân hệ E: Cơ chế phục hồi và tự sửa lỗi (Error Handling & Backoff)
* **Cơ chế Exponential Backoff (Thử lại tăng dần):** Khi gặp lỗi HTTP 429 (Too Many Requests), hệ thống không cố chấp gửi lại yêu cầu ngay lập tức. Thay vào đó, thời gian chờ thử lại sẽ nhân đôi sau mỗi lần thất bại (ví dụ: 1 phút -> 2 phút -> 4 phút -> nghỉ hẳn).
* **Danh sách hàng đợi lỗi (Dead Letter Queue):** Nếu một URL thử lại quá 3 lần thất bại, hệ thống tự động lưu URL đó vào file `failed_urls.txt` kèm mã lỗi, bỏ qua và chuyển sang video tiếp theo để không làm nghẽn tiến trình chung.