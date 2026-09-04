import os
import json
import time
from typing import List
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import create_client, Client

app = FastAPI(title="Abdullah Hariri Shipping")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

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

@app.get("/api/products")
def get_products():
    if not supabase:
        return []
    res = supabase.table("products").select("*").order("id", desc=True).execute()
    return res.data

@app.get("/api/orders")
def get_orders():
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
    files: List[UploadFile] = File(...)
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
    <style>body { font-family: 'Tajawal', sans-serif; }</style>
</head>
<body class="bg-gray-100 min-h-screen">

    <!-- شريط علوي -->
    <header class="bg-slate-900 text-white p-4 shadow-md sticky top-0 z-30 flex justify-between items-center">
        <div>
            <h1 class="text-base font-bold text-amber-400">عبدالله حريري للتوريد والشحن الدولي من الصين</h1>
            <p class="text-xs text-slate-400">كافة خدمات الشراء والفحص والشحن من كوانزو وإيوو</p>
        </div>
        <div class="flex gap-2">
            <button onclick="openAdminUpload()" class="bg-amber-600 text-white text-xs px-2.5 py-2 rounded-lg font-bold">
                + رفع بضاعة
            </button>
            <button onclick="openOrdersList()" class="bg-slate-700 text-white text-xs px-2.5 py-2 rounded-lg font-bold">
                📋 الطلبات
            </button>
        </div>
    </header>

    <!-- المعرض -->
    <main class="max-w-4xl mx-auto p-4 pb-20">
        <h2 class="text-base font-bold text-gray-800 mb-3">أحدث العروض والبضائع المتوفرة:</h2>
        <div id="products-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="col-span-full text-center py-10 text-gray-500">جاري تحميل البضائع...</div>
        </div>
    </main>

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
                <h3 class="text-base font-bold text-gray-900">طلبات عروض الأسعار المستلمة</h3>
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
        async function fetchProducts() {
            const res = await fetch('/api/products');
            const prods = await res.json();
            const container = document.getElementById('products-grid');
            if(!prods.length) {
                container.innerHTML = '<p class="text-center col-span-full py-8 text-gray-500">لا توجد بضائع منشورة بعد.</p>';
                return;
            }
            container.innerHTML = prods.map(p => {
                let mediaHtml = '';
                const mediaItems = p.media || (p.image_url ? [{url: p.image_url, type: 'image'}] : []);
                
                if (mediaItems.length > 0) {
                    mediaHtml = `
                    <div class="flex gap-2 overflow-x-auto p-2 bg-gray-50 border-b">
                        ${mediaItems.map(m => m.type === 'video' 
                            ? `<video src="${m.url}" controls class="h-44 w-64 object-cover rounded-lg shrink-0"></video>` 
                            : `<img src="${m.url}" class="h-44 w-64 object-cover rounded-lg shrink-0">`
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
            }).join('');
        }

        async function handleUpload(e) {
            e.preventDefault();
            const btn = document.getElementById('uploadBtn');
            btn.innerText = 'جاري الرفع... يرجى الانتظار';
            btn.disabled = true;

            const formData = new FormData();
            formData.append('title', document.getElementById('pTitle').value);
            formData.append('category', document.getElementById('pCat').value);
            formData.append('price', document.getElementById('pPrice').value);
            formData.append('moq', document.getElementById('pMoq').value);
            
            const fileInput = document.getElementById('pFiles');
            for(let i=0; i<fileInput.files.length; i++) {
                formData.append('files', fileInput.files[i]);
            }

            const res = await fetch('/api/products/upload', { method: 'POST', body: formData });
            if(res.ok) {
                alert('تم نشر البضاعة بالصور والفيديوهات بنجاح!');
                closeAdminUpload();
                fetchProducts();
            } else {
                alert('حدث خطأ أثناء الرفع، تأكد من إنشاء مجلد media في Supabase');
            }
            btn.innerText = 'رفع وحفظ الآن';
            btn.disabled = false;
        }

        async function openOrdersList() {
            document.getElementById('ordersModal').classList.remove('hidden');
            const list = document.getElementById('ordersListContent');
            list.innerHTML = 'جاري جلب الطلبات...';
            const res = await fetch('/api/orders');
            const orders = await res.json();
            if(!orders.length) {
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
            if(res.ok) {
                alert('تم إرسال طلب التسعيرة بنجاح! سيتم التواصل معكم من مكتب الصين.');
                closeOrderModal();
            }
        }

        fetchProducts();
    </script>
</body>
</html>
    """
