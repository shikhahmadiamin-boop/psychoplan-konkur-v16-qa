"use client";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
export type ChapterRow = { exam_body:string;degree:string;subject_code:string;chapter:string;topic:string;year_start:number;year_end:number;years_present:number;question_count:number;collection_id?:string;title?:string };
export type SubjectRow = { exam_body:string;degree:string;subject_code:string;year_start:number;year_end:number;years_present:number;question_count:number;collection_id?:string;title?:string };
export type FullRow = { exam_id:string;year:number;exam_body:string;degree:string;question_count:number };
export type FamilyRow = { family_id:string;label:string;question_count:number };
export type Catalog = { enabled:boolean; modes:{chapters:ChapterRow[];subjects:SubjectRow[];full_exams:FullRow[];families:FamilyRow[]} };
export function useKonkurCatalog(){
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState("");
  useEffect(()=>{
    let active=true;
    apiFetch("/api/konkur-bank/student/catalog/?collection_mode=independent_v15")
      .then(async r=>{if(!r.ok) throw Error(r.status===401?"برای مشاهده وارد حساب شوید.":"دریافت فهرست ممکن نشد.");return r.json();})
      .then((data:Catalog)=>{if(active)setCatalog(data);})
      .catch((e:Error)=>{if(active)setError(e.message);})
      .finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[]);
  return {catalog,loading,error};
}
