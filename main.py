import os
import json
import time
from typing import List, Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import create_client, Client

app = FastAPI(title="Abdullah Hariri Shipping")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY", "")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


class OrderCreate(BaseModel):
    customer_name: str
    customer_phone: str
    destination_country: str
    product_id: int
    quantity: int
    notes: str = ""


class AdminLogin(BaseModel):
    key: str


def verify_admin(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")):
    """
    يتحقق من صلاحية المدير عبر مقارنة الترويسة X-Admin-Key بقيمة
    متغير البيئة ADMIN_SECRET_KEY. يجب استدعاؤه عبر Depends() في أي
    مسار (route) يخص لوحة الإدارة فقط.
    """
    if not ADMIN_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_SECRET_KEY غير مضبوط على الخادم (Render Environment Variables)"
        )
    if not x_admin_key or x_admin_key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=401, detail="غير مصرح لك بالدخول - رمز الإدارة غير صحيح")
    return True


@app.post("/api/admin/login")
def admin_login(payload: AdminLogin):
    """
    مسار تسجيل دخول المدير. يستخدم للتحقق من كلمة المرور قبل حفظها
    في المتصفح (localStorage) على جهاز المدير، وأيضاً لإعادة التحقق
    من صلاحية الجلسة المحفوظة عند إعادة فتح الصفحة.
    """
    if not ADMIN_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_SECRET_KEY غير مضبوط على الخادم (Render Environment Variables)"
        )
    if payload.key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=401, detail="كلمة المرور غير صحيحة")
    return {"status": "success"}


@app.get("/api/products")
def get_products(limit: int = 8, offset: int = 0):
    """
    يدعم التحميل المجزأ (Pagination) عبر limit و offset.
    مثال: /api/products?limit=8&offset=0 ثم /api/products?limit=8&offset=8
    """
    if not supabase:
        return []
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    res = (
        supabase.table("products")
        .select("*")
        .order("id", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return res.data


@app.get("/api/orders")
def get_orders(admin: bool = Depends(verify_admin)):
    if not supabase:
        return []
    res = supabase.table("orders").select("*, products(title)").order("id", desc=True).execute()
    return res.data


@app.post("/api/orders")
def create_order(order: OrderCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database error")
    res = supabase.table("orders").insert(order.model_dump()).execute()
    return {"status": "success", "data": res.data}


@app.post("/api/products/upload")
async def create_product_with_media(
    title: str = Form(...),
    category: str = Form(...),
    price: float = Form(...),
    moq: int = Form(...),
    files: List[UploadFile] = File(...),
    admin: bool = Depends(verify_admin)
):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    media_urls = []
    for file in files:
        contents = await file.read()
        file_ext = file.filename.split(".")[-1]
        unique_name = f"{int(time.time()*1000)}_{file.filename}"
        mime_type = file.content_type or "image/jpeg"

        # رفع الملف لـ Supabase Storage
        supabase.storage.from_("media").upload(
            path=unique_name,
            file=contents,
            file_options={"content-type": mime_type}
        )

        # استخراج الرابط المباشر
        public_url = supabase.storage.from_("media").get_public_url(unique_name)
        is_video = "video" in mime_type.lower()
        media_urls.append({"url": public_url, "type": "video" if is_video else "image"})

    # إدخال البضاعة
    prod_data = {
        "title": title,
        "category": category,
        "price": price,
        "moq": moq,
        "image_url": media_urls[0]["url"] if media_urls else "",
        "media": media_urls
    }
    res = supabase.table("products").insert(prod_data).execute()
    return {"status": "success", "data": res.data}


@app.get("/", response_class=HTMLResponse)
def serve_home():
    return """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>مكتب عبدالله حريري للتوريد والشحن الدولي</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Tajawal', sans-serif; }
        #uploadProgressBar { transition: width 0.15s ease; }
    </style>
</head>
<body class="bg-gray-100 min-h-screen">

    <!-- شريط علوي -->
    <header class="bg-slate-900 text-white p-4 shadow-md sticky top-0 z-30 flex justify-between items-center">
        <div>
            <h1 class="text-base font-bold text-amber-400">عبدالله حريري للتوريد والشحن الدولي من الصين</h1>
            <p class="text-xs text-slate-400">كافة خدمات الشراء والفحص والشحن من كوانزو وإيوو</p>
        </div>
        <div id="headerAdminArea" class="flex gap-2 items-center"></div>
    </header>

    <!-- المعرض -->
    <main class="max-w-4xl mx-auto p-4 pb-20">
        <h2 class="text-base font-bold text-gray-800 mb-3">أحدث العروض والبضائع المتوفرة:</h2>
        <div id="products-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="col-span-full text-center py-10 text-gray-500">جاري تحميل البضائع...</div>
        </div>

        <!-- زر تحميل المزيد + نقطة مراقبة التمرير اللانهائي -->
        <div class="text-center mt-5">
            <button id="loadMoreBtn" onclick="loadMoreProducts()" class="hidden bg-white border border-gray-300 text-gray-700 text-sm px-5 py-2.5 rounded-lg font-bold shadow-sm hover:bg-gray-50">
                تحميل المزيد من البضائع
            </button>
            <p id="noMoreText" class="hidden text-xs text-gray-400 py-2">لا توجد بضائع إضافية</p>
        </div>
        <div id="scrollSentinel" class="h-2"></div>
    </main>

    <!-- زر دخول خفي في أسفل الصفحة يفتح نافذة تسجيل دخول المدير -->
    <div class="fixed bottom-3 left-3 z-20">
        <button onclick="openAdminLogin()" id="lockBtn" class="bg-slate-800/70 hover:bg-slate-800 text-white text-xs w-8 h-8 rounded-full flex items-center justify-center shadow-lg">
            🔒
        </button>
    </div>

    <!-- نافذة تسجيل دخول المدير -->
    <div id="adminLoginModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-5 w-full max-w-xs shadow-2xl">
            <h3 class="text-base font-bold text-gray-900 mb-1">دخول لوحة التحكم</h3>
            <p class="text-xs text-gray-500 mb-3">هذه الصفحة مخصصة لمكتب الصين فقط</p>
            <form onsubmit="handleAdminLogin(event)" class="space-y-3">
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">كلمة مرور الإدارة</label>
                    <input type="password" id="adminKeyInput" required autocomplete="current-password" class="w-full border rounded-lg p-2 text-sm">
                </div>
                <p id="adminLoginError" class="hidden text-xs text-red-600"></p>
                <div class="flex gap-2 pt-1">
                    <button type="submit" id="adminLoginBtn" class="flex-1 bg-slate-900 text-white py-2 rounded-lg font-bold text-sm">دخول</button>
                    <button type="button" onclick="closeAdminLogin()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm">إلغاء</button>
                </div>
            </form>
        </div>
    </div>

    <!-- نافذة رفع بضاعة جديدة مع صور وفيديوهات -->
    <div id="adminUploadModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-5 w-full max-w-md shadow-2xl max-h-[90vh] overflow-y-auto">
            <h3 class="text-base font-bold text-gray-900 mb-3">مكتب الصين: رفع بضاعة جديدة</h3>
            <form onsubmit="handleUpload(event)" class="space-y-3">
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">اسم البضاعة</label>
                    <input type="text" id="pTitle" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-xs font-bold text-gray-700 mb-1">التصنيف</label>
                        <input type="text" id="pCat" required placeholder="أقمشة، إلكترونيات" class="w-full border rounded-lg p-2 text-sm">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-gray-700 mb-1">السعر التقريبي ($)</label>
                        <input type="number" step="0.01" id="pPrice" required class="w-full border rounded-lg p-2 text-sm">
                    </div>
                </div>
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">أقل كمية للطلب (MOQ)</label>
                    <input type="number" id="pMoq" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">حدد صور وفيديوهات البضاعة من الهاتف (متعدد)</label>
                    <input type="file" id="pFiles" multiple accept="image/*,video/*" required class="w-full border rounded-lg p-1.5 text-xs">
                </div>

                <!-- شريط تقدم الرفع الحقيقي -->
                <div id="uploadProgressWrap" class="hidden space-y-1">
                    <div class="flex justify-between text-xs text-gray-600">
                        <span id="uploadProgressLabel">جاري رفع الملفات...</span>
                        <span id="uploadProgressPercent">0%</span>
                    </div>
                    <div class="w-full bg-gray-200 rounded-full h-2.5 overflow-hidden">
                        <div id="uploadProgressBar" class="bg-amber-600 h-2.5 rounded-full" style="width:0%"></div>
                    </div>
                    <p class="text-[11px] text-red-500">لا تغلق الصفحة أو تنتقل عنها أثناء الرفع</p>
                </div>

                <div class="flex gap-2 pt-2">
                    <button type="submit" id="uploadBtn" class="flex-1 bg-amber-600 text-white py-2 rounded-lg font-bold text-sm">رفع وحفظ الآن</button>
                    <button type="button" onclick="closeAdminUpload()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm">إلغاء</button>
                </div>
            </form>
        </div>
    </div>

    <!-- نافذة لائحة الطلبات المستلمة -->
    <div id="ordersModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-5 w-full max-w-lg shadow-2xl max-h-[90vh] flex flex-col">
            <div class="flex justify-between items-center mb-3">
                <h3 class="text-base font-bold text-gray-900">الطلبات المستلمة</h3>
                <button onclick="closeOrdersList()" class="text-gray-500 font-bold">✕</button>
            </div>
            <div id="ordersListContent" class="space-y-3 overflow-y-auto flex-1 pr-1">
                جاري التحميل...
            </div>
        </div>
    </div>

    <!-- نافذة إرسال طلب عرض سعر الشحن -->
    <div id="orderModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-5 w-full max-w-md shadow-2xl">
            <h3 class="text-base font-bold text-gray-900 mb-1">طلب تسعيرة شحن وتوريد</h3>
            <p id="modalProductTitle" class="text-xs text-blue-600 font-bold mb-3"></p>
            <form onsubmit="submitOrder(event)" class="space-y-2.5">
                <input type="hidden" id="orderProductId">
                <div>
                    <label class="block text-xs text-gray-600 mb-1">اسم العميل / الشركة</label>
                    <input type="text" id="custName" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-gray-600 mb-1">رقم الهاتف أو الواتساب</label>
                    <input type="tel" id="custPhone" required class="w-full border rounded-lg p-2 text-sm text-left" placeholder="+963 / +971">
                </div>
                <div>
                    <label class="block text-xs text-gray-600 mb-1">دولة ومدينة الوصول</label>
                    <input type="text" id="custDest" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-gray-600 mb-1">الكمية المطلوبة</label>
                    <input type="number" id="custQty" required min="1" class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div class="flex gap-2 pt-2">
                    <button type="submit" class="flex-1 bg-emerald-600 text-white py-2 rounded-lg font-bold text-sm">إرسال الطلب</button>
                    <button type="button" onclick="closeOrderModal()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm">إلغاء</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        // ============ إعدادات التقسيم (Pagination) ============
        const PAGE_SIZE = 8;
        let currentOffset = 0;
        let isLoadingProducts = false;
        let reachedEnd = false;

        // ============ حالة تسجيل دخول المدير ============
        let isAdmin = false;

        function getAdminKey() {
            return localStorage.getItem('adminKey') || '';
        }

        function renderAdminHeader() {
            const area = document.getElementById('headerAdminArea');
            const lockBtn = document.getElementById('lockBtn');
            if (isAdmin) {
                area.innerHTML = `
                    <button onclick="openAdminUpload()" class="bg-amber-600 text-white text-xs px-2.5 py-2 rounded-lg font-bold">
                        + رفع بضاعة
                    </button>
                    <button onclick="openOrdersList()" class="bg-slate-700 text-white text-xs px-2.5 py-2 rounded-lg font-bold">
                        📋 الطلبات
                    </button>
                    <button onclick="adminLogout()" class="bg-slate-600 text-white text-xs px-2.5 py-2 rounded-lg font-bold">
                        خروج
                    </button>
                `;
                lockBtn.classList.add('hidden');
            } else {
                area.innerHTML = '';
                lockBtn.classList.remove('hidden');
            }
        }

        async function verifyAdminSession() {
            const key = getAdminKey();
            if (!key) { isAdmin = false; renderAdminHeader(); return; }
            try {
                const res = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({key})
                });
                if (res.ok) {
                    isAdmin = true;
                } else {
                    localStorage.removeItem('adminKey');
                    isAdmin = false;
                }
            } catch (e) {
                isAdmin = false;
            }
            renderAdminHeader();
        }

        function openAdminLogin() {
            document.getElementById('adminLoginError').classList.add('hidden');
            document.getElementById('adminKeyInput').value = '';
            document.getElementById('adminLoginModal').classList.remove('hidden');
        }
        function closeAdminLogin() {
            document.getElementById('adminLoginModal').classList.add('hidden');
        }

        async function handleAdminLogin(e) {
            e.preventDefault();
            const btn = document.getElementById('adminLoginBtn');
            const errEl = document.getElementById('adminLoginError');
            errEl.classList.add('hidden');
            const key = document.getElementById('adminKeyInput').value;
            btn.disabled = true;
            btn.innerText = 'جاري التحقق...';
            try {
                const res = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({key})
                });
                if (res.ok) {
                    localStorage.setItem('adminKey', key);
                    isAdmin = true;
                    renderAdminHeader();
                    closeAdminLogin();
                } else {
                    errEl.innerText = 'كلمة المرور غير صحيحة';
                    errEl.classList.remove('hidden');
                }
            } catch (e2) {
                errEl.innerText = 'تعذر الاتصال بالخادم';
                errEl.classList.remove('hidden');
            }
            btn.disabled = false;
            btn.innerText = 'دخول';
        }

        function adminLogout() {
            localStorage.removeItem('adminKey');
            isAdmin = false;
            renderAdminHeader();
        }

        // إذا رفض الخادم طلباً بسبب صلاحية منتهية، أعد فتح نافذة الدخول
        function handleAuthFailure() {
            localStorage.removeItem('adminKey');
            isAdmin = false;
            renderAdminHeader();
            alert('انتهت صلاحية جلسة الإدارة، يرجى تسجيل الدخول من جديد');
            openAdminLogin();
        }

        // ============ عرض المنتجات مع التحميل المجزأ ============
        function renderProductCard(p) {
            let mediaHtml = '';
            const mediaItems = p.media || (p.image_url ? [{url: p.image_url, type: 'image'}] : []);

            if (mediaItems.length > 0) {
                mediaHtml = `
                <div class="flex gap-2 overflow-x-auto p-2 bg-gray-50 border-b">
                    ${mediaItems.map(m => m.type === 'video'
                        ? `<video src="${m.url}" controls preload="none" loading="lazy" class="h-44 w-64 object-cover rounded-lg shrink-0"></video>`
                        : `<img src="${m.url}" loading="lazy" class="h-44 w-64 object-cover rounded-lg shrink-0">`
                    ).join('')}
                </div>`;
            }

            return `
            <div class="bg-white rounded-xl shadow-sm border overflow-hidden flex flex-col justify-between">
                ${mediaHtml}
                <div class="p-4">
                    <span class="bg-amber-100 text-amber-800 text-xs px-2 py-0.5 rounded font-bold">${p.category}</span>
                    <h3 class="font-bold text-gray-900 mt-2 text-base">${p.title}</h3>
                    <p class="text-emerald-700 font-bold mt-1 text-lg">$${p.price} <span class="text-xs text-gray-400 font-normal">/ للقطعة تقريباً</span></p>
                    <p class="text-xs text-gray-500 mt-1">الحد الأدنى للطلب: <span class="font-bold text-gray-700">${p.moq} قطعة</span></p>
                    <button onclick="openOrderModal(${p.id}, '${p.title}', ${p.moq})" class="mt-4 w-full bg-slate-900 hover:bg-slate-800 text-white text-sm py-2.5 rounded-lg font-bold">
                        طلب تسعيرة شحن للبضاعة
                    </button>
                </div>
            </div>`;
        }

        async function fetchProducts() {
            currentOffset = 0;
            reachedEnd = false;
            const container = document.getElementById('products-grid');
            container.innerHTML = '<div class="col-span-full text-center py-10 text-gray-500">جاري تحميل البضائع...</div>';
            document.getElementById('noMoreText').classList.add('hidden');

            isLoadingProducts = true;
            const res = await fetch(`/api/products?limit=${PAGE_SIZE}&offset=0`);
            const prods = await res.json();
            isLoadingProducts = false;

            if (!prods.length) {
                container.innerHTML = '<p class="text-center col-span-full py-8 text-gray-500">لا توجد بضائع منشورة بعد.</p>';
                document.getElementById('loadMoreBtn').classList.add('hidden');
                return;
            }

            container.innerHTML = prods.map(renderProductCard).join('');
            currentOffset = prods.length;

            if (prods.length < PAGE_SIZE) {
                reachedEnd = true;
                document.getElementById('loadMoreBtn').classList.add('hidden');
                document.getElementById('noMoreText').classList.remove('hidden');
            } else {
                document.getElementById('loadMoreBtn').classList.remove('hidden');
            }
        }

        async function loadMoreProducts() {
            if (isLoadingProducts || reachedEnd) return;
            isLoadingProducts = true;
            const btn = document.getElementById('loadMoreBtn');
            const originalText = btn.innerText;
            btn.innerText = 'جاري التحميل...';
            btn.disabled = true;

            const res = await fetch(`/api/products?limit=${PAGE_SIZE}&offset=${currentOffset}`);
            const prods = await res.json();

            const container = document.getElementById('products-grid');
            container.insertAdjacentHTML('beforeend', prods.map(renderProductCard).join(''));
            currentOffset += prods.length;

            if (prods.length < PAGE_SIZE) {
                reachedEnd = true;
                btn.classList.add('hidden');
                document.getElementById('noMoreText').classList.remove('hidden');
            } else {
                btn.innerText = originalText;
                btn.disabled = false;
            }
            isLoadingProducts = false;
        }

        // تمرير لا نهائي: يراقب عنصراً بأسفل الصفحة ويحمّل المزيد تلقائياً
        const scrollObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting && !isLoadingProducts && !reachedEnd) {
                    loadMoreProducts();
                }
            });
        }, { rootMargin: '200px' });
        scrollObserver.observe(document.getElementById('scrollSentinel'));

        // ============ رفع بضاعة جديدة مع شريط تقدم حقيقي ============
        function handleUpload(e) {
            e.preventDefault();
            const btn = document.getElementById('uploadBtn');
            const progressWrap = document.getElementById('uploadProgressWrap');
            const progressBar = document.getElementById('uploadProgressBar');
            const progressPercent = document.getElementById('uploadProgressPercent');
            const progressLabel = document.getElementById('uploadProgressLabel');

            btn.innerText = 'جاري الرفع... يرجى الانتظار';
            btn.disabled = true;
            progressWrap.classList.remove('hidden');
            progressBar.style.width = '0%';
            progressPercent.innerText = '0%';
            progressLabel.innerText = 'جاري رفع الملفات...';

            const formData = new FormData();
            formData.append('title', document.getElementById('pTitle').value);
            formData.append('category', document.getElementById('pCat').value);
            formData.append('price', document.getElementById('pPrice').value);
            formData.append('moq', document.getElementById('pMoq').value);

            const fileInput = document.getElementById('pFiles');
            for (let i = 0; i < fileInput.files.length; i++) {
                formData.append('files', fileInput.files[i]);
            }

            // منع إغلاق الصفحة بالخطأ أثناء الرفع
            const beforeUnloadHandler = function (ev) {
                ev.preventDefault();
                ev.returnValue = '';
            };
            window.addEventListener('beforeunload', beforeUnloadHandler);

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/products/upload', true);
            xhr.setRequestHeader('X-Admin-Key', getAdminKey());

            xhr.upload.onprogress = function (event) {
                if (event.lengthComputable) {
                    const percent = Math.round((event.loaded / event.total) * 100);
                    progressBar.style.width = percent + '%';
                    progressPercent.innerText = percent + '%';
                    progressLabel.innerText = percent < 100 ? 'جاري رفع الملفات...' : 'جاري الحفظ في قاعدة البيانات...';
                }
            };

            xhr.onload = function () {
                window.removeEventListener('beforeunload', beforeUnloadHandler);
                if (xhr.status >= 200 && xhr.status < 300) {
                    alert('تم نشر البضاعة بالصور والفيديوهات بنجاح!');
                    closeAdminUpload();
                    fetchProducts();
                } else if (xhr.status === 401) {
                    handleAuthFailure();
                } else {
                    alert('حدث خطأ أثناء الرفع، تأكد من إنشاء مجلد media في Supabase');
                }
                resetUploadForm();
            };

            xhr.onerror = function () {
                window.removeEventListener('beforeunload', beforeUnloadHandler);
                alert('تعذر الاتصال بالخادم أثناء الرفع، تحقق من اتصال الإنترنت');
                resetUploadForm();
            };

            function resetUploadForm() {
                btn.innerText = 'رفع وحفظ الآن';
                btn.disabled = false;
                progressWrap.classList.add('hidden');
            }

            xhr.send(formData);
        }

        // ============ لائحة الطلبات (محمية) ============
        async function openOrdersList() {
            document.getElementById('ordersModal').classList.remove('hidden');
            const list = document.getElementById('ordersListContent');
            list.innerHTML = 'جاري جلب الطلبات...';
            const res = await fetch('/api/orders', {
                headers: {'X-Admin-Key': getAdminKey()}
            });
            if (res.status === 401) {
                closeOrdersList();
                handleAuthFailure();
                return;
            }
            const orders = await res.json();
            if (!orders.length) {
                list.innerHTML = '<p class="text-center py-6 text-gray-500 text-sm">لا توجد طلبات مستلمة بعد.</p>';
                return;
            }
            list.innerHTML = orders.map(o => `
                <div class="border rounded-xl p-3 bg-gray-50 text-xs space-y-1">
                    <div class="flex justify-between font-bold text-gray-800 text-sm">
                        <span>${o.customer_name}</span>
                        <span class="text-emerald-700">${o.quantity} قطعة</span>
                    </div>
                    <div class="text-gray-600">البضاعة: <span class="font-bold text-blue-600">${o.products?.title || 'منتج'}</span></div>
                    <div class="text-gray-600">الهاتف: <a href="tel:${o.customer_phone}" class="underline text-blue-700">${o.customer_phone}</a></div>
                    <div class="text-gray-600">وجهة الشحن: <strong>${o.destination_country}</strong></div>
                </div>
            `).join('');
        }

        function closeOrdersList() { document.getElementById('ordersModal').classList.add('hidden'); }
        function openAdminUpload() { document.getElementById('adminUploadModal').classList.remove('hidden'); }
        function closeAdminUpload() { document.getElementById('adminUploadModal').classList.add('hidden'); }
        function openOrderModal(id, title, moq) {
            document.getElementById('orderProductId').value = id;
            document.getElementById('modalProductTitle').innerText = title;
            document.getElementById('custQty').value = moq;
            document.getElementById('orderModal').classList.remove('hidden');
        }
        function closeOrderModal() { document.getElementById('orderModal').classList.add('hidden'); }

        async function submitOrder(e) {
            e.preventDefault();
            const body = {
                product_id: parseInt(document.getElementById('orderProductId').value),
                customer_name: document.getElementById('custName').value,
                customer_phone: document.getElementById('custPhone').value,
                destination_country: document.getElementById('custDest').value,
                quantity: parseInt(document.getElementById('custQty').value)
            };
            const res = await fetch('/api/orders', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });
            if (res.ok) {
                alert('تم إرسال طلب التسعيرة بنجاح! سيتم التواصل معكم من مكتب الصين.');
                closeOrderModal();
            }
        }

        // ============ بدء التشغيل ============
        renderAdminHeader();
        verifyAdminSession();
        fetchProducts();
    </script>
</body>
</html>
    """
