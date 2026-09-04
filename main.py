import os
import json
import time
from datetime import datetime, timezone
from typing import List, Optional, Literal
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Depends
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from supabase import create_client, Client

app = FastAPI(title="Abdullah Hariri Shipping")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY", "")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


class PriceTier(BaseModel):
    """شريحة سعر واحدة حسب الكمية، على طريقة Alibaba."""
    min_qty: int
    max_qty: Optional[int] = None  # None = فأكثر (لا يوجد حد أعلى)
    price: float


class OrderCreate(BaseModel):
    customer_name: str
    customer_phone: str
    destination_country: str
    product_id: int
    quantity: int
    notes: str = ""


class AdminLogin(BaseModel):
    key: str


class ProductUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    price_tiers: Optional[List[PriceTier]] = None


class OrderStatusUpdate(BaseModel):
    status: Literal["pending", "processing", "completed"]


def normalize_tiers(raw_tiers: List[dict]) -> List[dict]:
    """
    يرتب شرائح الأسعار تصاعدياً حسب الكمية الدنيا، ويتحقق من صحتها.
    """
    if not raw_tiers:
        raise HTTPException(status_code=400, detail="أدخل شريحة سعر واحدة على الأقل")
    tiers = []
    for t in raw_tiers:
        try:
            min_qty = int(t.get("min_qty"))
            price = float(t.get("price"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="بيانات شريحة السعر غير صحيحة")
        max_qty = t.get("max_qty")
        max_qty = int(max_qty) if max_qty not in (None, "", "null") else None
        if min_qty < 1 or price < 0:
            raise HTTPException(status_code=400, detail="قيم شريحة السعر غير منطقية")
        if max_qty is not None and max_qty < min_qty:
            raise HTTPException(status_code=400, detail="الحد الأعلى للكمية أصغر من الحد الأدنى في إحدى الشرائح")
        tiers.append({"min_qty": min_qty, "max_qty": max_qty, "price": price})
    tiers.sort(key=lambda t: t["min_qty"])
    return tiers


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
def get_products(limit: int = 8, offset: int = 0, q: Optional[str] = None):
    """
    يدعم التحميل المجزأ (Pagination) عبر limit و offset، وكذلك البحث
    الاختياري ضمن عناوين البضائع عبر معامل q (بحث جزئي غير حساس لحالة الأحرف).
    مثال: /api/products?limit=8&offset=0&q=قميص
    """
    if not supabase:
        return []
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    query = supabase.table("products").select("*").order("id", desc=True)
    if q:
        query = query.ilike("title", f"%{q.strip()}%")
    res = query.range(offset, offset + limit - 1).execute()
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
    order_data = order.model_dump()
    order_data["status"] = "pending"
    res = supabase.table("orders").insert(order_data).execute()
    return {"status": "success", "data": res.data}


@app.patch("/api/orders/{order_id}/status")
def update_order_status(order_id: int, payload: OrderStatusUpdate, admin: bool = Depends(verify_admin)):
    """
    تحديث حالة الطلب: pending (جديد) -> processing (قيد المعالجة) -> completed (مكتمل).
    عند وضع الحالة "مكتمل" يُسجَّل تاريخ ووقت الإكمال تلقائياً.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    update_data = {"status": payload.status}
    if payload.status == "completed":
        update_data["completed_at"] = datetime.now(timezone.utc).isoformat()
    else:
        update_data["completed_at"] = None

    res = supabase.table("orders").update(update_data).eq("id", order_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="الطلب غير موجود")
    return {"status": "success", "data": res.data}


@app.post("/api/products/upload")
async def create_product_with_media(
    title: str = Form(...),
    category: str = Form(...),
    price_tiers: str = Form(...),  # JSON string: [{"min_qty":1,"max_qty":99,"price":5.0}, ...]
    files: List[UploadFile] = File(...),
    admin: bool = Depends(verify_admin)
):
    """
    ينشئ بضاعة جديدة مع شرائح أسعار متدرجة حسب الكمية (بدل سعر وحيد + MOQ ثابت).
    price_tiers يصل كنص JSON من نموذج الرفع (multipart/form-data).
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    try:
        raw_tiers = json.loads(price_tiers)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="صيغة شرائح الأسعار غير صحيحة")

    tiers = normalize_tiers(raw_tiers)
    moq = tiers[0]["min_qty"]

    media_urls = []
    for file in files:
        contents = await file.read()
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
        "moq": moq,
        "price_tiers": tiers,
        "image_url": media_urls[0]["url"] if media_urls else "",
        "media": media_urls
    }
    res = supabase.table("products").insert(prod_data).execute()
    return {"status": "success", "data": res.data}


@app.put("/api/products/{product_id}")
def update_product(product_id: int, payload: ProductUpdate, admin: bool = Depends(verify_admin)):
    """
    تعديل بيانات بضاعة موجودة (الاسم، التصنيف، شرائح الأسعار).
    عند تعديل شرائح الأسعار، يُعاد حساب MOQ تلقائياً من أدنى شريحة.
    لا يتعامل هذا المسار مع الصور/الفيديوهات - تعديل الوسائط يتم بحذف
    البضاعة وإعادة رفعها من جديد في هذه النسخة.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="لا توجد بيانات لتحديثها")

    if "price_tiers" in update_data:
        tiers = normalize_tiers(update_data["price_tiers"])
        update_data["price_tiers"] = tiers
        update_data["moq"] = tiers[0]["min_qty"]

    res = supabase.table("products").update(update_data).eq("id", product_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="البضاعة غير موجودة")
    return {"status": "success", "data": res.data}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, admin: bool = Depends(verify_admin)):
    """
    حذف بضاعة نهائياً من قاعدة البيانات، مع محاولة حذف ملفاتها
    (صور/فيديوهات) من Supabase Storage تلقائياً لتوفير المساحة.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    existing = supabase.table("products").select("media").eq("id", product_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="البضاعة غير موجودة")

    media_items = existing.data[0].get("media") or []
    file_paths = []
    for m in media_items:
        url = m.get("url", "")
        if url:
            file_paths.append(url.split("/")[-1])

    if file_paths:
        try:
            supabase.storage.from_("media").remove(file_paths)
        except Exception:
            # لا نمنع حذف السجل من قاعدة البيانات حتى لو فشل حذف الملفات من التخزين
            pass

    supabase.table("products").delete().eq("id", product_id).execute()
    return {"status": "success"}


@app.delete("/api/orders/{order_id}")
def delete_order(order_id: int, admin: bool = Depends(verify_admin)):
    """حذف طلب تسعيرة بعد معالجته من قبل مكتب الصين."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    supabase.table("orders").delete().eq("id", order_id).execute()
    return {"status": "success"}


@app.get("/office-hero.webp")
def serve_hero_image():
    """
    يخدم صورة الهيرو مباشرة من جذر المشروع (بجانب main.py)، بدون مجلد static.
    الملف لازم يكون موجود بنفس مستوى main.py باسم office-hero.webp.
    """
    if not os.path.exists("office-hero.webp"):
        raise HTTPException(status_code=404, detail="ملف الصورة غير موجود على الخادم")
    return FileResponse("office-hero.webp", media_type="image/webp")


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
        /* خلفية الهيرو: صورة المكتب بالحجم الكامل، ثابتة أثناء التمرير */
        #heroBg {
            position: fixed;
            inset: 0;
            z-index: -1;
        }
        #heroBg img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: top center;
        }
        #heroBg .heroOverlay {
            position: absolute;
            inset: 0;
            background: linear-gradient(to bottom, rgba(15,23,42,0.55), rgba(15,23,42,0.75));
        }
        /* المحتوى الأبيض يعلو فوق الهيرو الثابت أثناء التمرير */
        #contentWrap {
            position: relative;
            z-index: 10;
            background: #fff;
        }
    </style>
</head>
<body class="min-h-screen">

    <!-- خلفية الهيرو الثابتة: صورة المكتب بالحجم الكامل -->
    <div id="heroBg">
        <img src="/office-hero.webp" alt="مكتب عبدالله حريري للتوريد والشحن">
        <div class="heroOverlay"></div>
    </div>

    <!-- الهيدر: شفاف فوق الصورة أول ما تفتح الصفحة، وبيصير له خلفية غامقة تلقائياً بعد ما يتغطى الهيرو بالتمرير عشان يضل مقروء -->
    <header id="mainHeader" class="sticky top-0 z-30 bg-transparent transition-colors duration-300">
        <div class="p-4 flex justify-between items-center">
            <div>
                <h1 class="text-base font-bold text-amber-400" style="text-shadow: 0 2px 8px rgba(0,0,0,0.85), 0 1px 2px rgba(0,0,0,0.9);">عبدالله حريري للتوريد والشحن الدولي من الصين</h1>
                <p class="text-xs text-white" style="text-shadow: 0 1px 6px rgba(0,0,0,0.85), 0 1px 2px rgba(0,0,0,0.9);">كافة خدمات الشراء والفحص والشحن من كوانزو وإيوو</p>
            </div>
            <div id="headerAdminArea" class="flex gap-2 items-center"></div>
        </div>
        <div class="px-4 pb-3">
            <div class="relative">
                <input type="text" id="searchInput" oninput="handleSearchInput()"
                       placeholder="ابحث عن بضاعة بالاسم..."
                       class="w-full bg-white/95 text-gray-800 text-sm rounded-lg py-2 pr-9 pl-3 shadow-lg focus:outline-none focus:ring-2 focus:ring-amber-500">
                <span class="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">🔎</span>
            </div>
        </div>
    </header>

    <!-- مساحة فارغة تكشف صورة الهيرو قبل أن يعلوها المحتوى الأبيض -->
    <div style="height: 55vh;"></div>

    <!-- المحتوى: خلفية بيضاء، بيصعد ويغطي على صورة الهيرو الثابتة أثناء التمرير -->
    <div id="contentWrap" class="rounded-t-3xl shadow-[0_-15px_30px_rgba(0,0,0,0.15)] min-h-screen">

    <!-- المعرض -->
    <main class="max-w-4xl mx-auto p-4 pb-20">
        <h2 class="text-base font-bold text-gray-800 mb-3 pt-2">أحدث العروض والبضائع المتوفرة:</h2>
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
    </div><!-- إغلاق contentWrap -->

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

    <!-- نافذة رفع بضاعة جديدة مع صور وفيديوهات وشرائح أسعار -->
    <div id="adminUploadModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-5 w-full max-w-md shadow-2xl max-h-[90vh] overflow-y-auto">
            <h3 class="text-base font-bold text-gray-900 mb-3">مكتب الصين: رفع بضاعة جديدة</h3>
            <form onsubmit="handleUpload(event)" class="space-y-3">
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">اسم البضاعة</label>
                    <input type="text" id="pTitle" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">التصنيف</label>
                    <input type="text" id="pCat" required placeholder="أقمشة، إلكترونيات" class="w-full border rounded-lg p-2 text-sm">
                </div>

                <!-- شرائح الأسعار حسب الكمية -->
                <div>
                    <div class="flex justify-between items-center mb-1">
                        <label class="block text-xs font-bold text-gray-700">شرائح الأسعار حسب الكمية</label>
                        <button type="button" onclick="addTierRow('pTiersContainer')" class="text-xs text-amber-700 font-bold">+ إضافة شريحة</button>
                    </div>
                    <p class="text-[11px] text-gray-400 mb-1.5">مثال: 1-99 قطعة = $5 / 100-499 قطعة = $4.5 / 500 فأكثر = $4</p>
                    <div id="pTiersContainer" class="space-y-1.5"></div>
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

    <!-- نافذة تعديل بضاعة موجودة -->
    <div id="editProductModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-5 w-full max-w-md shadow-2xl max-h-[90vh] overflow-y-auto">
            <h3 class="text-base font-bold text-gray-900 mb-3">تعديل بيانات البضاعة</h3>
            <form onsubmit="handleEditProduct(event)" class="space-y-3">
                <input type="hidden" id="editProductId">
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">اسم البضاعة</label>
                    <input type="text" id="ePTitle" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs font-bold text-gray-700 mb-1">التصنيف</label>
                    <input type="text" id="ePCat" required class="w-full border rounded-lg p-2 text-sm">
                </div>

                <div>
                    <div class="flex justify-between items-center mb-1">
                        <label class="block text-xs font-bold text-gray-700">شرائح الأسعار حسب الكمية</label>
                        <button type="button" onclick="addTierRow('ePTiersContainer')" class="text-xs text-amber-700 font-bold">+ إضافة شريحة</button>
                    </div>
                    <div id="ePTiersContainer" class="space-y-1.5"></div>
                </div>

                <p id="editProductError" class="hidden text-xs text-red-600"></p>
                <div class="flex gap-2 pt-2">
                    <button type="submit" id="editProductBtn" class="flex-1 bg-blue-600 text-white py-2 rounded-lg font-bold text-sm">حفظ التعديلات</button>
                    <button type="button" onclick="closeEditProduct()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm">إلغاء</button>
                </div>
                <p class="text-[11px] text-gray-400">لتغيير الصور أو الفيديوهات: احذف هذه البضاعة وارفعها من جديد.</p>
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
                    <input type="number" id="custQty" required min="1" oninput="updateEstimatedPrice()" class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div id="estimatedPriceBox" class="hidden bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs rounded-lg p-2"></div>
                <div class="flex gap-2 pt-2">
                    <button type="submit" class="flex-1 bg-emerald-600 text-white py-2 rounded-lg font-bold text-sm">إرسال الطلب</button>
                    <button type="button" onclick="closeOrderModal()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm">إلغاء</button>
                </div>
            </form>
        </div>
    </div>

    <!-- نافذة معرض الصور والفيديوهات بالحجم الكامل مع التكبير -->
    <div id="lightboxModal" class="fixed inset-0 bg-black/95 hidden z-[60] flex flex-col select-none">
        <div class="flex justify-between items-center p-3 text-white text-sm shrink-0">
            <span id="lightboxCounter" class="font-bold"></span>
            <div class="flex gap-2">
                <button onclick="lightboxZoomOut()" class="w-9 h-9 bg-white/10 rounded-full text-lg">➖</button>
                <button onclick="lightboxZoomIn()" class="w-9 h-9 bg-white/10 rounded-full text-lg">➕</button>
                <button onclick="closeLightbox()" class="w-9 h-9 bg-white/10 rounded-full text-lg">✕</button>
            </div>
        </div>
        <div id="lightboxContent"
             class="flex-1 relative overflow-auto flex items-center justify-center"
             ontouchstart="lightboxTouchStart(event)"
             ontouchend="lightboxTouchEnd(event)">
        </div>
        <button id="lightboxPrev" onclick="lightboxPrev()" class="absolute left-2 top-1/2 -translate-y-1/2 w-11 h-11 bg-white/10 text-white rounded-full text-2xl">‹</button>
        <button id="lightboxNext" onclick="lightboxNext()" class="absolute right-2 top-1/2 -translate-y-1/2 w-11 h-11 bg-white/10 text-white rounded-full text-2xl">›</button>
    </div>

    <script>
        // ============ إعدادات التقسيم (Pagination) والبحث ============
        const PAGE_SIZE = 8;
        let currentOffset = 0;
        let isLoadingProducts = false;
        let reachedEnd = false;
        let currentSearchQuery = '';
        let searchDebounceTimer = null;

        function handleSearchInput() {
            clearTimeout(searchDebounceTimer);
            searchDebounceTimer = setTimeout(() => {
                currentSearchQuery = document.getElementById('searchInput').value.trim();
                fetchProducts();
            }, 350);
        }

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
                    fetchProducts();
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
            fetchProducts();
        }

        // إذا رفض الخادم طلباً بسبب صلاحية منتهية، أعد فتح نافذة الدخول
        function handleAuthFailure() {
            localStorage.removeItem('adminKey');
            isAdmin = false;
            renderAdminHeader();
            fetchProducts();
            alert('انتهت صلاحية جلسة الإدارة، يرجى تسجيل الدخول من جديد');
            openAdminLogin();
        }

        // ============ شرائح الأسعار: صفوف ديناميكية (إضافة/حذف) ============
        let tierRowSeq = 0;

        function tierRowHtml(min, max, price) {
            const idx = tierRowSeq++;
            return `
            <div class="flex gap-1.5 items-center tier-row" data-idx="${idx}">
                <input type="number" min="1" placeholder="من (قطعة)" value="${min ?? ''}" class="tier-min w-1/4 border rounded-lg p-1.5 text-xs">
                <span class="text-xs text-gray-400">-</span>
                <input type="number" min="1" placeholder="إلى (فارغ = فأكثر)" value="${max ?? ''}" class="tier-max flex-1 border rounded-lg p-1.5 text-xs">
                <input type="number" step="0.01" min="0" placeholder="السعر $" value="${price ?? ''}" required class="tier-price w-1/4 border rounded-lg p-1.5 text-xs">
                <button type="button" onclick="removeTierRow(this)" class="text-red-500 text-xs px-1 shrink-0">✕</button>
            </div>`;
        }

        function addTierRow(containerId, min, max, price) {
            document.getElementById(containerId).insertAdjacentHTML('beforeend', tierRowHtml(min, max, price));
        }

        function removeTierRow(btn) {
            const container = btn.closest('div[id]') || btn.parentElement.parentElement;
            const rows = container.querySelectorAll('.tier-row');
            if (rows.length <= 1) { alert('يجب أن تبقى شريحة سعر واحدة على الأقل'); return; }
            btn.closest('.tier-row').remove();
        }

        function resetTiersContainer(containerId, tiers) {
            const container = document.getElementById(containerId);
            container.innerHTML = '';
            if (tiers && tiers.length) {
                tiers.forEach(t => addTierRow(containerId, t.min_qty, t.max_qty, t.price));
            } else {
                addTierRow(containerId);
            }
        }

        function collectTiers(containerId) {
            const rows = document.querySelectorAll(`#${containerId} .tier-row`);
            const tiers = [];
            for (const row of rows) {
                const min_qty = parseInt(row.querySelector('.tier-min').value);
                const maxRaw = row.querySelector('.tier-max').value;
                const price = parseFloat(row.querySelector('.tier-price').value);
                if (!min_qty || isNaN(price)) {
                    throw new Error('تأكد من تعبئة "من" و"السعر" في كل شريحة');
                }
                tiers.push({
                    min_qty,
                    max_qty: maxRaw ? parseInt(maxRaw) : null,
                    price
                });
            }
            tiers.sort((a, b) => a.min_qty - b.min_qty);
            return tiers;
        }

        // ============ عرض المنتجات مع التحميل المجزأ ============
        const mediaStore = {};
        const tierStore = {};

        function renderPriceTable(tiers) {
            if (!tiers || !tiers.length) {
                return '<p class="text-xs text-gray-400 mt-2">لا توجد أسعار محددة</p>';
            }
            const sorted = [...tiers].sort((a, b) => a.min_qty - b.min_qty);
            return `
            <table class="w-full text-xs mt-2 border rounded-lg overflow-hidden">
                <thead class="bg-gray-50 text-gray-500">
                    <tr>
                        <th class="text-right p-1.5 font-bold">الكمية (قطعة)</th>
                        <th class="text-right p-1.5 font-bold">السعر للقطعة</th>
                    </tr>
                </thead>
                <tbody>
                    ${sorted.map(t => `
                        <tr class="border-t">
                            <td class="p-1.5 text-gray-700">${t.max_qty ? `${t.min_qty} - ${t.max_qty}` : `${t.min_qty}+ فأكثر`}</td>
                            <td class="p-1.5 font-bold text-emerald-700">$${Number(t.price).toFixed(2)}</td>
                        </tr>`).join('')}
                </tbody>
            </table>`;
        }

        function renderProductCard(p) {
            let mediaHtml = '';
            const mediaItems = p.media || (p.image_url ? [{url: p.image_url, type: 'image'}] : []);
            mediaStore[p.id] = mediaItems;
            tierStore[p.id] = p.price_tiers || [];

            if (mediaItems.length > 0) {
                mediaHtml = `
                <div class="flex gap-2 overflow-x-auto p-2 bg-gray-50 border-b">
                    ${mediaItems.map((m, idx) => m.type === 'video'
                        ? `<video src="${m.url}" preload="none" loading="lazy" onclick="openLightbox(${p.id}, ${idx})" class="h-44 w-64 object-cover rounded-lg shrink-0 cursor-pointer"></video>`
                        : `<img src="${m.url}" loading="lazy" onclick="openLightbox(${p.id}, ${idx})" class="h-44 w-64 object-cover rounded-lg shrink-0 cursor-pointer">`
                    ).join('')}
                </div>`;
            }

            const adminControls = isAdmin ? `
                <div class="flex gap-2 mt-2">
                    <button onclick='openEditProduct(${JSON.stringify(p)})' class="flex-1 bg-blue-50 text-blue-700 border border-blue-200 text-xs py-1.5 rounded-lg font-bold">
                        ✏️ تعديل
                    </button>
                    <button onclick="deleteProduct(${p.id}, '${(p.title || '').replace(/'/g, "\\'")}')" class="flex-1 bg-red-50 text-red-700 border border-red-200 text-xs py-1.5 rounded-lg font-bold">
                        🗑️ حذف
                    </button>
                </div>` : '';

            return `
            <div class="bg-white rounded-xl shadow-sm border overflow-hidden flex flex-col justify-between">
                ${mediaHtml}
                <div class="p-4">
                    <span class="bg-amber-100 text-amber-800 text-xs px-2 py-0.5 rounded font-bold">${p.category}</span>
                    <h3 class="font-bold text-gray-900 mt-2 text-base">${p.title}</h3>
                    ${renderPriceTable(p.price_tiers)}
                    <p class="text-xs text-gray-500 mt-2">الحد الأدنى للطلب: <span class="font-bold text-gray-700">${p.moq} قطعة</span></p>
                    <button onclick="openOrderModal(${p.id}, '${(p.title || '').replace(/'/g, "\\'")}', ${p.moq})" class="mt-4 w-full bg-slate-900 hover:bg-slate-800 text-white text-sm py-2.5 rounded-lg font-bold">
                        طلب تسعيرة شحن للبضاعة
                    </button>
                    ${adminControls}
                </div>
            </div>`;
        }

        // ============ معرض الصور والفيديوهات بالحجم الكامل (Lightbox) ============
        let lightboxMedia = [];
        let lightboxIndex = 0;
        let lightboxZoom = 1;
        let lightboxTouchStartX = 0;

        function openLightbox(productId, index) {
            lightboxMedia = mediaStore[productId] || [];
            if (!lightboxMedia.length) return;
            lightboxIndex = index;
            renderLightboxItem();
            document.getElementById('lightboxModal').classList.remove('hidden');
        }

        function closeLightbox() {
            const video = document.querySelector('#lightboxContent video');
            if (video) video.pause();
            document.getElementById('lightboxModal').classList.add('hidden');
        }

        function renderLightboxItem() {
            const item = lightboxMedia[lightboxIndex];
            const content = document.getElementById('lightboxContent');
            lightboxZoom = 1;

            if (item.type === 'video') {
                content.innerHTML = `<video src="${item.url}" controls autoplay class="max-h-full max-w-full"></video>`;
            } else {
                content.innerHTML = `<img id="lightboxImg" src="${item.url}" ondblclick="lightboxToggleZoom()" class="max-h-[85vh] max-w-full object-contain transition-transform duration-150" style="transform: scale(1)">`;
            }

            document.getElementById('lightboxCounter').innerText = `${lightboxIndex + 1} / ${lightboxMedia.length}`;
            const showNav = lightboxMedia.length > 1;
            document.getElementById('lightboxPrev').style.display = showNav ? 'flex' : 'none';
            document.getElementById('lightboxNext').style.display = showNav ? 'flex' : 'none';
        }

        function lightboxPrev() {
            lightboxIndex = (lightboxIndex - 1 + lightboxMedia.length) % lightboxMedia.length;
            renderLightboxItem();
        }
        function lightboxNext() {
            lightboxIndex = (lightboxIndex + 1) % lightboxMedia.length;
            renderLightboxItem();
        }

        function applyLightboxZoom() {
            const img = document.getElementById('lightboxImg');
            if (img) img.style.transform = `scale(${lightboxZoom})`;
        }
        function lightboxZoomIn() {
            lightboxZoom = Math.min(lightboxZoom + 0.5, 3);
            applyLightboxZoom();
        }
        function lightboxZoomOut() {
            lightboxZoom = Math.max(lightboxZoom - 0.5, 1);
            applyLightboxZoom();
        }
        function lightboxToggleZoom() {
            lightboxZoom = lightboxZoom > 1 ? 1 : 2;
            applyLightboxZoom();
        }

        // تمرير باللمس للتنقل بين الصور (يتعطل أثناء التكبير حتى يتيح التمرير الطبيعي للاستكشاف)
        function lightboxTouchStart(e) {
            lightboxTouchStartX = e.touches[0].clientX;
        }
        function lightboxTouchEnd(e) {
            if (lightboxZoom > 1) return;
            const diff = e.changedTouches[0].clientX - lightboxTouchStartX;
            if (Math.abs(diff) > 50) {
                if (diff > 0) lightboxPrev(); else lightboxNext();
            }
        }

        // التنقل بلوحة المفاتيح على الحاسوب
        document.addEventListener('keydown', function (e) {
            if (document.getElementById('lightboxModal').classList.contains('hidden')) return;
            if (e.key === 'Escape') closeLightbox();
            else if (e.key === 'ArrowLeft') lightboxPrev();
            else if (e.key === 'ArrowRight') lightboxNext();
        });

        async function fetchProducts() {
            currentOffset = 0;
            reachedEnd = false;
            const container = document.getElementById('products-grid');
            container.innerHTML = '<div class="col-span-full text-center py-10 text-gray-500">جاري تحميل البضائع...</div>';
            document.getElementById('noMoreText').classList.add('hidden');

            const qParam = currentSearchQuery ? `&q=${encodeURIComponent(currentSearchQuery)}` : '';
            isLoadingProducts = true;
            const res = await fetch(`/api/products?limit=${PAGE_SIZE}&offset=0${qParam}`);
            const prods = await res.json();
            isLoadingProducts = false;

            if (!prods.length) {
                container.innerHTML = currentSearchQuery
                    ? `<p class="text-center col-span-full py-8 text-gray-500">ما في نتائج لـ "${currentSearchQuery}"</p>`
                    : '<p class="text-center col-span-full py-8 text-gray-500">لا توجد بضائع منشورة بعد.</p>';
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

            const qParam = currentSearchQuery ? `&q=${encodeURIComponent(currentSearchQuery)}` : '';
            const res = await fetch(`/api/products?limit=${PAGE_SIZE}&offset=${currentOffset}${qParam}`);
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

            let tiers;
            try {
                tiers = collectTiers('pTiersContainer');
            } catch (err) {
                alert(err.message);
                return;
            }

            btn.innerText = 'جاري الرفع... يرجى الانتظار';
            btn.disabled = true;
            progressWrap.classList.remove('hidden');
            progressBar.style.width = '0%';
            progressPercent.innerText = '0%';
            progressLabel.innerText = 'جاري رفع الملفات...';

            const formData = new FormData();
            formData.append('title', document.getElementById('pTitle').value);
            formData.append('category', document.getElementById('pCat').value);
            formData.append('price_tiers', JSON.stringify(tiers));

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
                    let msg = 'حدث خطأ أثناء الرفع، تأكد من إنشاء مجلد media في Supabase';
                    try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) {}
                    alert(msg);
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

        // ============ تعديل وحذف البضاعة (للمدير فقط) ============
        function openEditProduct(p) {
            document.getElementById('editProductId').value = p.id;
            document.getElementById('ePTitle').value = p.title;
            document.getElementById('ePCat').value = p.category;
            resetTiersContainer('ePTiersContainer', p.price_tiers);
            document.getElementById('editProductError').classList.add('hidden');
            document.getElementById('editProductModal').classList.remove('hidden');
        }
        function closeEditProduct() {
            document.getElementById('editProductModal').classList.add('hidden');
        }

        async function handleEditProduct(e) {
            e.preventDefault();
            const btn = document.getElementById('editProductBtn');
            const errEl = document.getElementById('editProductError');
            errEl.classList.add('hidden');

            let tiers;
            try {
                tiers = collectTiers('ePTiersContainer');
            } catch (err) {
                errEl.innerText = err.message;
                errEl.classList.remove('hidden');
                return;
            }

            const id = document.getElementById('editProductId').value;
            const body = {
                title: document.getElementById('ePTitle').value,
                category: document.getElementById('ePCat').value,
                price_tiers: tiers
            };
            btn.disabled = true;
            btn.innerText = 'جاري الحفظ...';
            try {
                const res = await fetch(`/api/products/${id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Admin-Key': getAdminKey()
                    },
                    body: JSON.stringify(body)
                });
                if (res.ok) {
                    closeEditProduct();
                    fetchProducts();
                } else if (res.status === 401) {
                    closeEditProduct();
                    handleAuthFailure();
                } else {
                    let msg = 'تعذر حفظ التعديلات';
                    try { msg = (await res.json()).detail || msg; } catch (e2) {}
                    errEl.innerText = msg;
                    errEl.classList.remove('hidden');
                }
            } catch (e2) {
                errEl.innerText = 'تعذر الاتصال بالخادم';
                errEl.classList.remove('hidden');
            }
            btn.disabled = false;
            btn.innerText = 'حفظ التعديلات';
        }

        async function deleteProduct(id, title) {
            if (!confirm(`هل أنت متأكد من حذف "${title}" نهائياً؟ لا يمكن التراجع عن هذا الإجراء.`)) return;
            const res = await fetch(`/api/products/${id}`, {
                method: 'DELETE',
                headers: {'X-Admin-Key': getAdminKey()}
            });
            if (res.ok) {
                fetchProducts();
            } else if (res.status === 401) {
                handleAuthFailure();
            } else {
                alert('تعذر حذف البضاعة');
            }
        }

        // ============ لائحة الطلبات (محمية) ============
        function formatOrderDate(iso) {
            if (!iso) return '';
            const d = new Date(iso);
            const datePart = d.toLocaleDateString('ar-EG', {year: 'numeric', month: 'short', day: 'numeric'});
            const timePart = d.toLocaleTimeString('ar-EG', {hour: '2-digit', minute: '2-digit'});
            return `${datePart} - ${timePart}`;
        }

        function renderOrderCard(o) {
            let statusAction = '';
            if (o.status === 'completed') {
                statusAction = `<span class="text-emerald-700 text-[11px] font-bold">✅ اكتمل بتاريخ: ${formatOrderDate(o.completed_at)}</span>`;
            } else if (o.status === 'processing') {
                statusAction = `<button onclick="updateOrderStatus(${o.id}, 'completed')" class="text-emerald-700 text-[11px] font-bold">✅ إكمال الطلب</button>`;
            } else {
                statusAction = `<button onclick="updateOrderStatus(${o.id}, 'processing')" class="text-amber-700 text-[11px] font-bold">▶️ بدء المعالجة</button>`;
            }

            return `
                <div class="border rounded-xl p-3 bg-gray-50 text-xs space-y-1">
                    <div class="flex justify-between font-bold text-gray-800 text-sm">
                        <span>${o.customer_name}</span>
                        <span class="text-emerald-700">${o.quantity} قطعة</span>
                    </div>
                    <div class="text-gray-600">البضاعة: <span class="font-bold text-blue-600">${o.products?.title || 'منتج'}</span></div>
                    <div class="text-gray-600">الهاتف: <a href="tel:${o.customer_phone}" class="underline text-blue-700">${o.customer_phone}</a></div>
                    <div class="text-gray-600">وجهة الشحن: <strong>${o.destination_country}</strong></div>
                    <div class="flex justify-between items-center pt-1">
                        ${statusAction}
                        <button onclick="deleteOrder(${o.id})" class="text-red-600 text-[11px] font-bold">🗑️ حذف</button>
                    </div>
                </div>`;
        }

        function renderOrdersSection(title, orders, emptyText) {
            return `
                <div>
                    <h4 class="text-xs font-bold text-gray-500 mb-2">${title} (${orders.length})</h4>
                    <div class="space-y-2">
                        ${orders.length ? orders.map(renderOrderCard).join('') : `<p class="text-center text-gray-400 text-[11px] py-3">${emptyText}</p>`}
                    </div>
                </div>`;
        }

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

            const pending = orders.filter(o => !o.status || o.status === 'pending');
            const processing = orders.filter(o => o.status === 'processing');
            const completed = orders.filter(o => o.status === 'completed');

            list.innerHTML = `
                <div class="space-y-5">
                    ${renderOrdersSection('🆕 طلبات جديدة', pending, 'لا توجد طلبات جديدة')}
                    ${renderOrdersSection('⏳ قيد المعالجة حالياً', processing, 'لا توجد طلبات قيد المعالجة')}
                    ${renderOrdersSection('✅ طلبات مكتملة', completed, 'لا توجد طلبات مكتملة بعد')}
                </div>`;
        }

        async function updateOrderStatus(id, status) {
            const res = await fetch(`/api/orders/${id}/status`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Admin-Key': getAdminKey()
                },
                body: JSON.stringify({status})
            });
            if (res.ok) {
                openOrdersList();
            } else if (res.status === 401) {
                closeOrdersList();
                handleAuthFailure();
            } else {
                alert('تعذر تحديث حالة الطلب');
            }
        }

        async function deleteOrder(id) {
            if (!confirm('هل تم التعامل مع هذا الطلب وتريد حذفه من القائمة؟')) return;
            const res = await fetch(`/api/orders/${id}`, {
                method: 'DELETE',
                headers: {'X-Admin-Key': getAdminKey()}
            });
            if (res.ok) {
                openOrdersList();
            } else if (res.status === 401) {
                closeOrdersList();
                handleAuthFailure();
            } else {
                alert('تعذر حذف الطلب');
            }
        }

        function closeOrdersList() { document.getElementById('ordersModal').classList.add('hidden'); }

        function openAdminUpload() {
            resetTiersContainer('pTiersContainer', null);
            document.getElementById('adminUploadModal').classList.remove('hidden');
        }
        function closeAdminUpload() { document.getElementById('adminUploadModal').classList.add('hidden'); }

        // ============ نافذة الطلب + حساب السعر التقديري حسب الكمية ============
        let currentOrderTiers = [];

        function getPriceForQty(qty) {
            if (!currentOrderTiers.length) return null;
            const sorted = [...currentOrderTiers].sort((a, b) => a.min_qty - b.min_qty);
            let applicable = null;
            for (const t of sorted) {
                if (qty >= t.min_qty && (t.max_qty == null || qty <= t.max_qty)) {
                    applicable = t;
                }
            }
            // إذا كانت الكمية أكبر من كل الشرائح المحدودة، استخدم آخر شريحة (الأعلى)
            if (!applicable) applicable = sorted[sorted.length - 1];
            return applicable;
        }

        function updateEstimatedPrice() {
            const qty = parseInt(document.getElementById('custQty').value) || 0;
            const box = document.getElementById('estimatedPriceBox');
            const tier = getPriceForQty(qty);
            if (tier && qty > 0) {
                box.innerHTML = `السعر التقديري للقطعة: <span class="font-bold">$${tier.price.toFixed(2)}</span> — الإجمالي التقريبي: <span class="font-bold">$${(tier.price * qty).toFixed(2)}</span>`;
                box.classList.remove('hidden');
            } else {
                box.classList.add('hidden');
            }
        }

        function openOrderModal(id, title, moq) {
            document.getElementById('orderProductId').value = id;
            document.getElementById('modalProductTitle').innerText = title;
            document.getElementById('custQty').value = moq;
            currentOrderTiers = tierStore[id] || [];
            updateEstimatedPrice();
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
        (async function init() {
            await verifyAdminSession();
            fetchProducts();
        })();

        // الهيدر شفاف فوق الصورة بالأول، وبيصير له خلفية غامقة بعد ما المحتوى يبلش يغطي الهيرو، عشان يضل النص مقروء
        const mainHeaderEl = document.getElementById('mainHeader');
        function updateHeaderOnScroll() {
            if (window.scrollY > window.innerHeight * 0.35) {
                mainHeaderEl.classList.remove('bg-transparent');
                mainHeaderEl.classList.add('bg-slate-900/90', 'backdrop-blur-sm', 'shadow-md');
            } else {
                mainHeaderEl.classList.add('bg-transparent');
                mainHeaderEl.classList.remove('bg-slate-900/90', 'backdrop-blur-sm', 'shadow-md');
            }
        }
        window.addEventListener('scroll', updateHeaderOnScroll, { passive: true });
        updateHeaderOnScroll();
    </script>
</body>
</html>
    """
