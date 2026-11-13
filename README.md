# Agent Builder

Service แยกสำหรับสร้างและปรับ DeepAgent ด้วยฟอร์ม โดยไม่ต้องแก้โค้ดหรือ deploy ใหม่
ต่อเข้า Portal เป็น app ตัวหนึ่งแบบเดียวกับ Todo: login ผ่าน Keycloak ของ Portal และ Portal เป็นคนสร้าง route ให้

Backend ยกมาจาก [poc-deep-agent](https://github.com/BeagleSoCute/poc-deep-agent) ส่วน frontend เขียนใหม่

## รันบนเครื่องตัวเอง (ไม่มี login)

ต้องมี [uv](https://docs.astral.sh/uv/) และ Node 22

```bash
cd backend && uv sync && AGENTS_AUTH_DISABLED=1 ALLOW_PYTHON_TOOLS=1 uv run uvicorn app.main:app --port 8100
```

```bash
cd frontend && npm install && npm run dev
```

เปิด <http://127.0.0.1:5190> Vite ส่งต่อ `/api`, `/auth` และ `/downloads` ไปที่ backend พอร์ต 8100
(พอร์ต 8000 เว้นไว้ให้ Portal)

`AGENTS_AUTH_DISABLED=1` ปิด login ทุกคนที่เข้าพอร์ตได้คือผู้ใช้ `demo` ใช้บนเครื่องตัวเองเท่านั้น
ถ้าไม่ตั้งค่านี้และยังไม่ได้ตั้งค่า login backend จะไม่ยอมเริ่มทำงาน

## รันเป็น app ใน Portal

ต้องมี stack ของ Portal รันอยู่ก่อน (`just up` และ `just seed` ใน app-portal)

1. **ลงทะเบียนใน Portal:** เข้า <http://portal.localhost> ด้วย admin แล้วสร้าง app
   ตั้ง slug เป็น `agents` และ "ที่อยู่ของ app" เป็น `http://agents:8000` เลือก group ที่ให้เข้าใช้
   แล้วคัดลอก client secret ที่แสดงครั้งเดียว
2. **ตั้งค่า:** คัดลอก `.env.example` เป็น `.env` ใส่ `AGENTS_CLIENT_SECRET` และ `AGENTS_TOKEN_ENCRYPTION_KEY`
3. **เริ่ม container:**

```bash
docker compose up -d --build --wait
```

4. เปิด <http://agents.localhost> หรือกด tile ใน Launcher ของ Portal

[docker-compose.yml](docker-compose.yml) เอา container เข้า network `portal-local_default` ของ Portal
ไม่มี label ของ Traefik และไม่เปิดพอร์ตออกมา ทางเข้าเดียวคือ route ที่ Portal สร้างจากที่อยู่ในข้อ 1

| เรื่อง | ทำอย่างไร |
|---|---|
| Login | โค้ดชุดเดียวกับ Todo ([backend/app/auth](backend/app/auth)): code flow + PKCE, session ฝั่ง server, refresh, back-channel logout |
| สิทธิ์ | ต้องมี client role `access` ของ `agents` ไม่มีจะถูกส่งไปหน้า `/denied` ของ Portal |
| API | ทุก route ใต้ `/api` และไฟล์ใต้ `/downloads` ต้องมี session การเขียนต้องมี `Origin` ตรงกับ `AGENTS_APP_URL` |
| Audit | ชื่อผู้กระทำมาจาก session ที่ login |
| Container | ไม่ได้รันเป็น root และเขียนได้เฉพาะ volume `/data` |

### ครั้งแรก: ตั้ง LLM

Agent ตัวอย่าง 4 ตัวถูกสร้างให้ตอน backend เริ่มทำงาน แต่ยังตอบไม่ได้จนกว่าจะมี connection

- **ไม่มี API key:** หน้า Connections กด "เพิ่ม mock connection สำหรับ demo" แล้วกด "ใช้กับ agent ทั้งหมด"
  mock model ตอบตามกฎตายตัว ([backend/app/mock_llm.py](backend/app/mock_llm.py)) ไม่ได้คิดเอง
  แต่เดินครบทุก flow ของ UI: stream ข้อความ, เรียก tool, หยุดรออนุมัติ
- **มี key หรือ gateway:** เพิ่ม connection ชนิด OpenAI-compatible, Anthropic หรือ Gemini แล้วกด "ทดสอบ"

## หน้าจอ

| ส่วน | ทำอะไรได้ |
|---|---|
| แถบซ้าย | รายการ agent สลับได้ในคลิกเดียว, สร้าง agent, ลิงก์ไป Connections และ Audit, ผู้ใช้และปุ่มออกจากระบบ |
| แชท (กลาง) | ทดลองคุยกับ agent ที่เปิดอยู่ เห็น tool ที่ถูกเรียก และการ์ดขออนุมัติพร้อมค่าที่จะส่ง |
| ตั้งค่า (ขวา) | การ์ดสรุปของ Model, Instructions, Tools, Guardrails, Skills, Subagents และขีดจำกัด กดเพื่อเปิดฟอร์มแก้ · บันทึกเป็นเวอร์ชันใหม่, เวอร์ชัน (rollback), JSON, ลบ agent |
| Connections | เพิ่ม, ทดสอบ, ตั้งให้ agent ทั้งหมด |
| Audit | run ล่าสุดพร้อมแถวของแต่ละ run, การแก้ config, ปุ่มพิสูจน์ว่าแก้และลบแถวไม่ได้ |

แชทส่ง draft ที่ยังไม่บันทึกไปด้วย จึงลองการแก้ได้ก่อนบันทึกเป็นเวอร์ชัน การ์ดที่ถูกแก้จะมีขอบสีและจุดกำกับ

## ลำดับ demo ที่แนะนำ

ข้อความด้านล่างใช้ได้กับ mock model

1. เลือก `support-triage` จากแถบซ้าย พิมพ์ `ลูกค้า cust-8842 แจ้งว่าล็อกอินไม่ได้` แชทจะหยุดรออนุมัติก่อนเรียก `mock_crm_lookup`
   กดอนุมัติ ผลที่ได้มีอีเมลและเบอร์โทรถูกปิดเป็น `[REDACTED]` จาก guardrail
2. เปิดการ์ด Tools ทางขวา เอาเครื่องหมาย "ต้องอนุมัติ" ออก กด "ห้องใหม่" แล้วส่งข้อความเดิม คราวนี้ไม่หยุดถาม โดยยังไม่ได้บันทึก
3. กด "เพิ่ม Python tool" ตั้งชื่อ `greet_user` กด "ลองรัน" แล้ว "เพิ่มเข้า agent" จากนั้นพิมพ์ `greet user สมชาย`
4. กด "บันทึก v…" แล้วเปิด "เวอร์ชัน" ที่ท้ายแผงตั้งค่าเพื่อ rollback
5. เลือก `pdf-generator` พิมพ์ `create pdf report: Q1 sales 120, Q2 sales 150` จะได้ปุ่มดาวน์โหลดไฟล์
6. เปิดหน้า Audit ดู run, tool call และการอนุมัติที่เพิ่งทำ

## ที่แก้จาก POC

- **การอนุมัติ tool:** `/api/runs/resume` ส่ง `{"decisions": [...]}` ชนิด `approve | edit | reject` ตาม langchain 1.x
  (ของเดิมส่ง `accept` แบบเก่าและใช้ไม่ได้กับ library ปัจจุบัน) API ยังรับคำว่า `accept` เหมือนเดิม
- **event `interrupt`:** ส่ง `action_requests` เป็น JSON จริง UI จึงแสดงชื่อ tool และ args ที่รออนุมัติได้
- **Guardrails 3 ตัว:** `tool_denylist` และ `tool_call_log` อ่านชื่อ tool ผิดที่ ส่วน `pii_redaction` ไม่เคยปิดข้อมูลจริง
  และ middleware สองตัวขึ้นไปใน agent เดียวกันทำให้สร้าง agent ไม่ได้ แก้ครบแล้ว
- **Connection ชนิด `mock`:** สำหรับ demo โดยไม่มี key
- **Login:** ทุก API อยู่หลัง login หน้า playground และ audit แบบ HTML เดิมถูกลบ เพราะ frontend ใหม่แทนที่แล้ว
- **Audit** เก็บใต้ `DATA_DIR` เหมือน store ตัวอื่น (ของเดิมเขียนลงโฟลเดอร์โค้ดเสมอ)
- **`fpdf`** อยู่ในรายการ import ที่ Python tool ใช้ได้ตั้งแต่ต้น เพราะ agent ตัวอย่างใช้

## ยังไม่ได้ทำ

- **Login ทั้ง flow ยังไม่ได้ทดสอบกับ Portal จริง:** ทดสอบแล้วว่าไม่มี session เข้าไม่ได้ และ container คุยกับ Keycloak กับ Traefik ได้
  แต่ยังไม่ได้ลงทะเบียน app ใน Portal จึงยังไม่เคย login สำเร็จจริงสักครั้ง
- **Back-channel logout บนเครื่อง:** Keycloak ต้อง resolve `agents.localhost` ได้ ซึ่งต้องเพิ่ม alias `agents.localhost`
  ให้ service `traefik` ใน `infra/docker-compose.yml` ของ app-portal ถ้าไม่เพิ่ม การ logout จาก Portal จะมีผลกับที่นี่เมื่อ access token หมดอายุ
- **แยกสิทธิ์:** ทุกคนที่มี role `access` แก้ agent, connection และ Python tool ได้เท่ากัน ยังไม่แยก "แก้ prompt" กับ "เพิ่ม tool"
- **Python tool** ยังไม่ใช่ sandbox จริง ใน container มันรันด้วยสิทธิ์เดียวกับ service และออกเน็ตได้
  จึงปิดเป็นค่าเริ่มต้น (`ALLOW_PYTHON_TOOLS`) ดูเงื่อนไขใน [backend/app/registry/python_tools.py](backend/app/registry/python_tools.py)
- **ประวัติแชท** อยู่ในหน่วยความจำ หายเมื่อ restart
- **Test อัตโนมัติ** ยังไม่มีทั้ง backend และ frontend

ข้อมูลตอนรันบนเครื่องอยู่ที่ `backend/data/` และ `backend/downloads/` ลบสองโฟลเดอร์นี้เพื่อเริ่มใหม่ ส่วนใน container อยู่ใน volume `agents-data`
