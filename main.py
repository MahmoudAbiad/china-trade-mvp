import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import create_client, Client

app = FastAPI(title="China Trade Platform")

# قراءة مفاتيح Supabase من متغيرات البيئة
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# نماذج البيانات
class ProductCreate(BaseModel):
    title: str
    category: str
    price: float
    moq: int
    image_url: str

class OrderCreate(BaseModel):
    customer_name: str
    customer_phone: str
    destination_country: str
    product_id: int
    quantity: int
    notes: str = ""

# API: جلب المنتجات
@app.get("/api/products")
def get_products():
    if not supabase:
        return []
    res = supabase.table("products").select("*").order("id", desc=True).execute()
    return res.data

# API: إضافة منتج (خاص بالمكتب)
@app.post("/api/products")
def add_product(prod: ProductCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = supabase.table("products").insert(prod.model_dump()).execute()
    return res.data

# API: إرسال طلب عرض سعر
@app.post("/api/orders")
def create_order(order: OrderCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = supabase.table("orders").insert(order.model_dump()).execute()
    return {"status": "success", "data": res.data}

# الصفحة الرئيسية: واجهة الزبائن + لوحة الإدارة (HTML + Tailwind)
@app.get("/", response_class=HTMLResponse)
def serve_home():
    return """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>بوابة الشحن المباشر من الصين</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
    <style>body { font-family: 'Tajawal', sans-serif; }</style>
</head>
<body class="bg-gray-100 min-h-screen pb-20">

    <!-- شريط علوي -->
    <header class="bg-slate-900 text-white p-4 shadow-md sticky top-0 z-30 flex justify-between items-center">
        <div>
            <h1 class="text-xl font-bold flex items-center gap-2">📦 خط الصين للشحن الدولي</h1>
            <p class="text-xs text-slate-400">توريد مباشر من مستودعات الصين</p>
        </div>
        <button onclick="toggleAdminModal()" class="bg-amber-600 hover:bg-amber-700 text-white text-xs px-3 py-2 rounded-lg font-bold">
            + إضافة بضاعة
        </button>
    </header>

    <!-- محتوى المنتجات -->
    <main class="max-w-4xl mx-auto p-4">
        <h2 class="text-lg font-bold text-gray-800 mb-3">الكتالوج المتوفر للشحن:</h2>
        <div id="products-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="col-span-full text-center py-10 text-gray-500">جاري تحميل البضائع...</div>
        </div>
    </main>

    <!-- نافذة طلب عرض السعر الشحن -->
    <div id="orderModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-6 w-full max-w-md shadow-2xl">
            <h3 class="text-lg font-bold text-gray-900 mb-2">طلب استيراد وشحن بضاعة</h3>
            <p id="modalProductTitle" class="text-sm text-blue-600 font-semibold mb-4"></p>
            <form onsubmit="submitOrder(event)" class="space-y-3">
                <input type="hidden" id="orderProductId">
                <div>
                    <label class="block text-xs text-gray-600 mb-1">اسمك الكامل / الشركة</label>
                    <input type="text" id="custName" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-gray-600 mb-1">رقم الهاتف أو واتساب</label>
                    <input type="tel" id="custPhone" required class="w-full border rounded-lg p-2 text-sm text-left" placeholder="+963 / +971">
                </div>
                <div>
                    <label class="block text-xs text-gray-600 mb-1">دولة ومدينة الوصول</label>
                    <input type="text" id="custDest" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-gray-600 mb-1">الكمية المطلوبة (بالقطع)</label>
                    <input type="number" id="custQty" required min="1" class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div class="flex gap-2 pt-2">
                    <button type="submit" class="flex-1 bg-emerald-600 text-white py-2 rounded-lg font-bold text-sm">إرسال الطلب للمكتب</button>
                    <button type="button" onclick="closeOrderModal()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm">إلغاء</button>
                </div>
            </form>
        </div>
    </div>

    <!-- نافذة لوحة الإدارة (إضافة بضاعة من الصين) -->
    <div id="adminModal" class="fixed inset-0 bg-black/60 hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl p-6 w-full max-w-md shadow-2xl">
            <h3 class="text-lg font-bold text-gray-900 mb-3">لوحة المكتب: رفع بضاعة جديدة</h3>
            <form onsubmit="submitProduct(event)" class="space-y-3">
                <div>
                    <label class="block text-xs text-gray-600 mb-1">اسم البضاعة</label>
                    <input type="text" id="prodTitle" required class="w-full border rounded-lg p-2 text-sm">
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-xs text-gray-600 mb-1">التصنيف</label>
                        <input type="text" id="prodCat" required class="w-full border rounded-lg p-2 text-sm" placeholder="أقمشة، إلكترونيات..">
                    </div>
                    <div>
                        <label class="block text-xs text-gray-600 mb-1">السعر التقريبي ($)</label>
                        <input type="number" step="0.01" id="prodPrice" required class="w-full border rounded-lg p-2 text-sm">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-xs text-gray-600 mb-1">أقل كمية (MOQ)</label>
                        <input type="number" id="prodMoq" required class="w-full border rounded-lg p-2 text-sm">
                    </div>
                    <div>
                        <label class="block text-xs text-gray-600 mb-1">رابط الصورة</label>
                        <input type="url" id="prodImg" required class="w-full border rounded-lg p-2 text-sm" placeholder="https://...">
                    </div>
                </div>
                <div class="flex gap-2 pt-3">
                    <button type="submit" class="flex-1 bg-amber-600 text-white py-2 rounded-lg font-bold text-sm">حفظ ونشر بالكتالوج</button>
                    <button type="button" onclick="toggleAdminModal()" class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm">إغلاق</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        async function fetchProducts() {
            try {
                const res = await fetch('/api/products');
                const products = await res.json();
                const container = document.getElementById('products-grid');
                if (products.length === 0) {
                    container.innerHTML = '<p class="text-center col-span-full py-8 text-gray-500">لا توجد بضائع معروضة حالياً.</p>';
                    return;
                }
                container.innerHTML = products.map(p => `
                    <div class="bg-white rounded-xl shadow-sm border overflow-hidden flex flex-col justify-between">
                        <img src="${p.image_url}" alt="${p.title}" class="h-44 w-full object-cover">
                        <div class="p-4 flex-1 flex flex-col justify-between">
                            <div>
                                <span class="bg-slate-100 text-slate-700 text-xs px-2 py-0.5 rounded font-medium">${p.category || 'عام'}</span>
                                <h3 class="font-bold text-gray-900 mt-2">${p.title}</h3>
                                <p class="text-emerald-700 font-bold mt-1 text-lg">$${p.price} <span class="text-xs text-gray-500 font-normal">/ للقطعة</span></p>
                                <p class="text-xs text-gray-500 mt-1">الحد الأدنى للطلب (MOQ): <span class="font-bold text-gray-700">${p.moq} قطعة</span></p>
                            </div>
                            <button onclick="openOrderModal(${p.id}, '${p.title}', ${p.moq})" class="mt-4 w-full bg-slate-900 hover:bg-slate-800 text-white text-sm py-2.5 rounded-lg font-bold transition">
                                طلب تسعيرة شحن
                            </button>
                        </div>
                    </div>
                `).join('');
            } catch (err) {
                document.getElementById('products-grid').innerHTML = '<p class="text-red-500 text-center col-span-full">حدث خطأ في تحميل البيانات.</p>';
            }
        }

        function openOrderModal(id, title, moq) {
            document.getElementById('orderProductId').value = id;
            document.getElementById('modalProductTitle').innerText = title;
            document.getElementById('custQty').value = moq;
            document.getElementById('orderModal').classList.remove('hidden');
        }

        function closeOrderModal() {
            document.getElementById('orderModal').classList.add('hidden');
        }

        function toggleAdminModal() {
            document.getElementById('adminModal').classList.toggle('hidden');
        }

        async function submitOrder(e) {
            e.preventDefault();
            const body = {
                product_id: parseInt(document.getElementById('orderProductId').value),
                customer_name: document.getElementById('custName').value,
                customer_phone: document.getElementById('custPhone').value,
                destination_country: document.getElementById('custDest').value,
                quantity: parseInt(document.getElementById('custQty').value),
            };
            const res = await fetch('/api/orders', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });
            if(res.ok) {
                alert('تم إرسال طلب التسعيرة بنجاح! سيتم مراجعته من مكتب الصين.');
                closeOrderModal();
            } else {
                alert('حدث خطأ في إرسال الطلب');
            }
        }

        async function submitProduct(e) {
            e.preventDefault();
            const body = {
                title: document.getElementById('prodTitle').value,
                category: document.getElementById('prodCat').value,
                price: parseFloat(document.getElementById('prodPrice').value),
                moq: parseInt(document.getElementById('prodMoq').value),
                image_url: document.getElementById('prodImg').value
            };
            const res = await fetch('/api/products', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });
            if(res.ok) {
                alert('تمت إضافة البضاعة بنجاح للكتالوج!');
                toggleAdminModal();
                fetchProducts();
            } else {
                alert('حدث خطأ أثناء الإضافة');
            }
        }

        fetchProducts();
    </script>
</body>
</html>
    """
