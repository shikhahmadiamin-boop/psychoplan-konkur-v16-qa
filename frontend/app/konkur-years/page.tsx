"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { BookOpen, CalendarDays, ClipboardList, Layers3, ArrowRight, Info } from "lucide-react";
import { useKonkurCatalog } from "@/components/konkur-bank/useKonkurCatalog";
const tabs = [
  {id:"chapters",title:"فصل‌به‌فصل",icon:Layers3,desc:"سؤالات واقعی بر اساس درس، فصل و مبحث کلی"},
  {id:"subjects",title:"جامع درس‌به‌درس",icon:BookOpen,desc:"سؤالات هر درس در بازه سالانه، بدون تکثیر سؤال"},
  {id:"full_exams",title:"کنکور کامل",icon:CalendarDays,desc:"فقط دفترچه‌هایی که کامل و رسمی تأیید شده‌اند"},
] as const;
type Mode = typeof tabs[number]["id"];
const bodyLabel=(s:string)=>s==='SCIENCE'?'وزارت علوم':s==='HEALTH'?'وزارت بهداشت':s;
const degreeLabel=(s:string)=>s==='MASTER'?'ارشد':s==='PHD'?'دکتری':s;
export default function KonkurYears(){
  const {catalog,loading,error}=useKonkurCatalog();
  const router=useRouter();const [starting,setStarting]=useState(false);const [startError,setStartError]=useState("");
  async function start(mode:Mode,row:Record<string,string|number>){
    setStarting(true);setStartError("");
    const selector=mode==="chapters"?{exam_body:row.exam_body,degree:row.degree,subject_code:row.subject_code,chapter:row.chapter,topic:row.topic,year_start:row.year_start,year_end:row.year_end}:mode==="subjects"?{exam_body:row.exam_body,degree:row.degree,subject_code:row.subject_code,year_start:row.year_start,year_end:row.year_end}:{exam_id:row.exam_id};
    if ((mode === "subjects" || mode === "chapters") && typeof row.collection_id === "string" && row.collection_id) {
      // Prepared packages are addressed by their own immutable collection ID.
      // No inference or cross-collection merging is done on the server.
      Object.assign(selector, {collection_id:row.collection_id});
    }
    try{const res=await apiFetch("/api/konkur-bank/student/start/",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode,selector})});const data=await res.json();if(!res.ok)throw Error(data.detail||"شروع آزمون ممکن نشد.");router.push(`/konkur-bank/attempt/${data.attempt_id}`);}catch(e){setStartError(e instanceof Error?e.message:"خطای ارتباط");}finally{setStarting(false);}
  }
  const [mode,setMode]=useState<Mode>("chapters");
  const [search,setSearch]=useState("");
  const raw = catalog?.modes[mode]||[];
  const rows=useMemo(()=>raw.filter((r)=>JSON.stringify(r).toLowerCase().includes(search.trim().toLowerCase())),[raw,search]);
  return <main dir="rtl" className="min-h-screen bg-slate-50 text-slate-900 px-4 py-7 sm:px-7 sm:py-9">
    <div className="mx-auto max-w-6xl">
      <Link href="/weekly-exams" className="inline-flex items-center gap-2 text-sm font-bold text-blue-700"><ArrowRight size={16}/> بازگشت به آزمون‌ها</Link>
      <header className="mt-5 rounded-3xl bg-gradient-to-l from-[#133a78] via-[#1d63c7] to-[#234c87] px-6 py-7 text-white sm:px-9 sm:py-10">
        <div className="text-xs font-bold text-orange-200">PSYCHOPLAN · OFFICIAL EXAMS</div>
        <h1 className="mt-2 text-2xl font-black sm:text-4xl">کنکورهای سنوات گذشته</h1>
        <p className="mt-3 max-w-3xl text-sm leading-7 text-blue-100">یک بانک مرکزی، سه شیوه تمرین: فصل‌به‌فصل، جامع هر درس و دفترچه کامل. سؤال‌ها فقط پس از تأیید علمی و بررسی مجوز انتشار ارائه می‌شوند.</p>
      </header>
      <div role="tablist" aria-label="نوع آزمون سنواتی" className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {tabs.map(tab=><button key={tab.id} type="button" role="tab" aria-selected={mode===tab.id} onClick={()=>{setMode(tab.id);setSearch("");}}
          className={`rounded-2xl border p-4 text-right transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${mode===tab.id?"border-blue-500 bg-blue-50 shadow-sm":"border-slate-200 bg-white hover:bg-slate-50"}`}>
          <tab.icon size={22} className="mb-2 text-blue-700"/>
          <span className="block font-black">{tab.title}</span><span className="mt-1 block text-xs leading-6 text-slate-500">{tab.desc}</span>
        </button>)}
      </div>
      <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 sm:p-7" aria-live="polite">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-lg font-black">{tabs.find(t=>t.id===mode)?.title}</h2>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="جستجو در مجموعه‌ها" aria-label="جستجو در مجموعه‌ها" className="w-full rounded-xl border border-slate-200 px-4 py-3 text-sm outline-blue-500 sm:w-72"/>
        </div>
        {startError&&<p role="alert" className="mt-3 text-red-700">{startError}</p>}
        {error ? <p role="alert" className="mt-5 rounded-xl bg-red-50 p-4 text-red-700">{error}</p> :
         loading ? <p className="mt-6 text-slate-500">در حال دریافت فهرست...</p> :
         !catalog?.enabled ? <div className="mt-6 rounded-xl bg-blue-50 p-5 text-sm leading-8 text-blue-900"><Info size={18} className="ml-2 inline"/> زیرساخت در حال آماده‌سازی است. هنوز هیچ آزمونی برای دانشجویان منتشر نشده.</div> :
         rows.length===0 ? <p className="mt-6 text-sm leading-8 text-slate-600">در این بخش فعلاً آزمون تأیید و منتشرشده‌ای وجود ندارد.</p> :
         <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
           {rows.map((item,i)=>{
             const r=item as unknown as Record<string,string|number>;
             return <article key={String(r.collection_id||r.exam_id||r.subject_code||r.topic||'item')+'-'+i} className="rounded-xl border border-slate-200 p-4">
               <h3 className="font-bold text-slate-900">{typeof r.title === "string" && r.title ? r.title : mode==='full_exams'?`کنکور ${degreeLabel(String(r.degree))} ${bodyLabel(String(r.exam_body))} ${r.year}`:String(r.subject_code||'')}</h3>
               {mode==='chapters'&&<p className="mt-2 text-sm text-slate-700">{r.chapter} · {r.topic}</p>}
               <p className="mt-2 text-xs text-slate-500">{mode==='full_exams'?String(r.exam_id):`${degreeLabel(String(r.degree))} ${bodyLabel(String(r.exam_body))} · ${r.year_start} تا ${r.year_end}`}</p>
               <p className="mt-2 text-sm font-bold text-blue-800">{r.question_count} سؤال</p>
               <button type="button" disabled={starting} onClick={()=>start(mode,r)} className="mt-3 rounded-xl bg-blue-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">شروع آزمون</button>
             </article>;
           })}
         </div>}
      </section>
    </div>
  </main>;
}
