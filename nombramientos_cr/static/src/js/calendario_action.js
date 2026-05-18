/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const DAYS_SHORT = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"];
const MONTHS_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                   "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"];
const EMP_COLORS = [
    "#378ADD","#1D9E75","#D85A30","#BA7517",
    "#D4537E","#7F77DD","#639922","#E24B4A",
    "#0F6E56","#993C1D","#3B6D11","#A32D2D",
];

function empColor(id){return EMP_COLORS[(id-1)%EMP_COLORS.length];}

function getMonday(baseDate, offset=0){
    const d=new Date(baseDate);
    const day=d.getDay();
    const diff=d.getDate()-(day===0?6:day-1);
    d.setDate(diff+offset*7);d.setHours(0,0,0,0);return d;
}
function toDateStr(d){return d.toISOString().split("T")[0];}
function fmtDate(d){return d.toLocaleDateString("es-CR",{day:"numeric",month:"short"});}
function floatToTime(h){
    const hh=Math.floor(h%24),mm=Math.round((h%1)*60);
    return String(hh).padStart(2,"0")+":"+String(mm).padStart(2,"0");
}
function timeToFloat(t){const[h,m]=t.split(":").map(Number);return h+m/60;}
function addDays(d,n){const r=new Date(d);r.setDate(r.getDate()+n);return r;}

class CalendarioNombramientos extends Component {
    static template="nombramientos_cr.Calendario";
    static props=["*"];

    setup(){
        this.orm=useService("orm");
        this.state=useState({
            viewMode:"week",        // week | biweek | month
            baseDate:new Date(),    // anchor date for navigation
            loading:true, sedeFilter:"all", modal:null, saving:false,
            data:{turnos:[],employees:[],branches:[],templates:[],
                  week_start:"",week_end:"",config:{}},
        });
        this.form=useState({
            emp_id:"",branch_id:"",date:"",hour_start:"08:00",
            hour_end:"17:00",rate:"",state:"present",notes:"",turno_id:null,
            sede_turno_id:null, errorMsg:'',
            multi_day:false, selected_days:["lun","mar","mie","jue","vie"],
        });
        onWillStart(()=>this.loadData());
    }

    // ── Date range helpers ───────────────────────────────────────────────────
    get rangeStart(){
        const vm=this.state.viewMode, bd=this.state.baseDate;
        if(vm==="month"){
            const d=new Date(bd.getFullYear(),bd.getMonth(),1);return d;
        }
        return getMonday(bd);
    }
    get rangeEnd(){
        const vm=this.state.viewMode, rs=this.rangeStart;
        if(vm==="month"){
            return new Date(rs.getFullYear(),rs.getMonth()+1,0);
        }
        if(vm==="biweek") return addDays(rs,13);
        return addDays(rs,6);
    }
    get dates(){
        const dates=[]; let d=new Date(this.rangeStart);
        while(d<=this.rangeEnd){dates.push(new Date(d));d.setDate(d.getDate()+1);}
        return dates;
    }
    // For month view: group dates by week rows
    get weekRows(){
        // Month view: build a proper calendar grid aligned to Mon–Sun
        // Pad the first week with nulls so day 1 lands on the right column
        const all=this.dates;
        if(!all.length) return [];

        const firstDay=all[0];
        // getDay(): 0=Sun,1=Mon...6=Sat; we want 0=Mon...6=Sun
        const startOffset=(firstDay.getDay()+6)%7; // 0=Mon

        // Build flat array with leading nulls
        const padded=[...Array(startOffset).fill(null),...all];
        // Fill trailing nulls to complete last week
        while(padded.length%7!==0) padded.push(null);

        const rows=[];
        for(let i=0;i<padded.length;i+=7) rows.push(padded.slice(i,i+7));
        return rows;
    }
    // For biweek: two week rows
    get biweekRows(){
        const all=this.dates; // 14 days
        return [all.slice(0,7), all.slice(7,14)];
    }
    get rangeLabel(){
        const vm=this.state.viewMode, rs=this.rangeStart, re=this.rangeEnd;
        if(vm==="month") return MONTHS_ES[rs.getMonth()]+" "+rs.getFullYear();
        return fmtDate(rs)+" — "+fmtDate(re);
    }

    // ── Navigation ───────────────────────────────────────────────────────────
    prev(){
        const d=new Date(this.state.baseDate);
        const vm=this.state.viewMode;
        if(vm==="month") d.setMonth(d.getMonth()-1);
        else if(vm==="biweek") d.setDate(d.getDate()-14);
        else d.setDate(d.getDate()-7);
        this.state.baseDate=d; this.loadData();
    }
    next(){
        const d=new Date(this.state.baseDate);
        const vm=this.state.viewMode;
        if(vm==="month") d.setMonth(d.getMonth()+1);
        else if(vm==="biweek") d.setDate(d.getDate()+14);
        else d.setDate(d.getDate()+7);
        this.state.baseDate=d; this.loadData();
    }
    setView(vm){this.state.viewMode=vm; this.loadData();}

    async loadData(){
        this.state.loading=true;
        const data=await this.orm.call(
            "nombramientos.calendario","get_week_data",
            [toDateStr(this.rangeStart), toDateStr(this.rangeEnd)]);
        this.state.data=data;
        this.state.loading=false;
    }

    // ── Summary ──────────────────────────────────────────────────────────────
    get summary(){
        const ts=this.state.data.turnos;
        const totalH=ts.reduce((a,t)=>a+(t.hours||0),0);
        const totalC=Math.round(ts.reduce((a,t)=>a+(t.amount||0),0));

        // By employee
        const byEmp={};
        ts.forEach(t=>{
            if(!byEmp[t.emp_id]) byEmp[t.emp_id]={name:t.emp_name,id:t.emp_id,hours:0,turnos:0,costo:0};
            byEmp[t.emp_id].hours+=t.hours||0;
            byEmp[t.emp_id].turnos+=1;
            byEmp[t.emp_id].costo+=t.amount||0;
        });

        // By sede
        const bySede={};
        ts.forEach(t=>{
            if(!bySede[t.sede]) bySede[t.sede]={name:t.sede,hours:0,turnos:0,costo:0};
            bySede[t.sede].hours+=t.hours||0;
            bySede[t.sede].turnos+=1;
            bySede[t.sede].costo+=t.amount||0;
        });

        return{
            turnos:ts.length,
            empleados:Object.keys(byEmp).length,
            horas:totalH.toFixed(1),
            costo:totalC.toLocaleString("es-CR"),
            byEmp:Object.values(byEmp).sort((a,b)=>b.hours-a.hours),
            bySede:Object.values(bySede).sort((a,b)=>b.hours-a.hours),
        };
    }
    get sedes(){
        const b=this.state.data.branches;
        return this.state.sedeFilter==="all"?b:b.filter(x=>x.id==this.state.sedeFilter);
    }
    turnosBySede(sedeName,date){
        const ds=toDateStr(date);
        return this.state.data.turnos.filter(t=>t.sede===sedeName&&t.date===ds);
    }
    isToday(d){const t=new Date();t.setHours(0,0,0,0);return d.getTime()===t.getTime();}
    empColor(id){return empColor(id);}
    floatToTime(h){return floatToTime(h);}
    dayLabel(d){return DAYS_SHORT[d.getDay()-1>=0?d.getDay()-1:6];}
    fmtDate(d){return fmtDate(d);}

    // ── Modal ────────────────────────────────────────────────────────────────
    openNew(d,sedeName){
        const branch=this.state.data.branches.find(b=>b.name===sedeName);
        const firstEmp=this.state.data.employees[0];
        Object.assign(this.form,{
            turno_id:null, sede_turno_id:null, errorMsg:'', emp_id:firstEmp?.id||"",
            branch_id:branch?.id||"", date:toDateStr(d),
            hour_start:"08:00", hour_end:"17:00", rate:"",
            state:"present", notes:"",
            multi_day:false, selected_days:["lun","mar","mie","jue","vie"],
        });
        this.state.modal="new";
        if(firstEmp) this.loadEmpRate(String(firstEmp.id));
    }
    openEdit(t){
        const branch=this.state.data.branches.find(b=>b.name===t.sede);
        Object.assign(this.form,{
            turno_id:t.id, emp_id:t.emp_id, branch_id:branch?.id||"",
            date:t.date, hour_start:floatToTime(t.hour_start),
            hour_end:floatToTime(t.hour_end), rate:t.rate||"",
            state:t.state||"present", notes:t.notes||"",
        });
        this.state.modal="edit";
    }
    closeModal(){this.state.modal=null;}

    async loadEmpRate(empId){
        const id=parseInt(empId); if(!id||isNaN(id))return;
        try{
            const res=await this.orm.call(
                "nombramientos.calendario","get_employee_rate",[id]);
            if(res&&res.rate!==undefined)
                this.form.rate=res.rate>0?res.rate:"";
        }catch(e){console.warn("rate error:",e);}
    }
    onEmpChange(ev){this.form.emp_id=ev.target.value;this.loadEmpRate(ev.target.value);}
    onMultiDayChange(ev){this.form.multi_day=ev.target.checked;}
    onDayCheck(ev,dayKey){
        const days=[...this.form.selected_days];
        if(ev.target.checked){if(!days.includes(dayKey))days.push(dayKey);}
        else{const i=days.indexOf(dayKey);if(i>=0)days.splice(i,1);}
        this.form.selected_days=[...days];
    }
    onTemplateChange(ev){
        const id=parseInt(ev.target.value); if(!id)return;
        const tpl=this.state.data.templates.find(t=>t.id===id); if(!tpl)return;
        this.form.hour_start=floatToTime(tpl.h_start);
        this.form.hour_end=floatToTime(tpl.h_end);
        this.form.sede_turno_id=null;
    }
    onSedeTurnoChange(ev){
        const id=parseInt(ev.target.value);
        this.form.sede_turno_id=id||null;
        if(!id)return;
        const sede=this.state.data.branches.find(b=>b.id==this.form.branch_id);
        if(!sede||!sede.turnos)return;
        const turno=sede.turnos.find(t=>t.id===id); if(!turno)return;
        this.form.hour_start=floatToTime(turno.h_start);
        this.form.hour_end=floatToTime(turno.h_end);
    }
    get sedeTurnos(){
        const sede=this.state.data.branches.find(b=>b.id==this.form.branch_id);
        return(sede&&sede.turnos)||[];
    }

    async saveModal(){
        this.state.saving=true;
        this.form.errorMsg='';
        const f=this.form;
        const base={
            emp_id:parseInt(f.emp_id), branch_id:parseInt(f.branch_id),
            hour_start:timeToFloat(f.hour_start), hour_end:timeToFloat(f.hour_end),
            rate:parseFloat(f.rate)||0, state:f.state, notes:f.notes,
            sede_turno_id:f.sede_turno_id||null,
        };
        const DAY_MAP={lun:0,mar:1,mie:2,jue:3,vie:4,sab:5,dom:6};
        try{
            if(f.turno_id){
                await this.orm.call("nombramientos.calendario","save_turno",
                    [{...base,id:f.turno_id,date:f.date}]);
            } else if(f.multi_day&&f.selected_days.length>0){
                const clickedDate=new Date(f.date+"T00:00:00");
                const mon=getMonday(clickedDate);
                const vals_list=f.selected_days.map(dk=>{
                    const d=new Date(mon);d.setDate(d.getDate()+(DAY_MAP[dk]??0));
                    return{...base,date:toDateStr(d)};
                });
                await this.orm.call("nombramientos.calendario","save_turnos_batch",[vals_list]);
            } else {
                await this.orm.call("nombramientos.calendario","save_turno",
                    [{...base,date:f.date}]);
            }
            this.state.modal=null;
            await this.loadData();
        }catch(e){
            // Odoo RPC errors: e.data.message (server) or e.message (network)
            let msg='Error al guardar el turno.';
            try{
                // Try all possible locations for the error message
                const raw=e?.data?.message||e?.data?.debug||e?.message||String(e)||'';
                if(raw){
                    // Split on newlines, find first non-empty meaningful line
                    const lines=raw.split(/\n/).map(l=>l.trim()).filter(l=>l.length>3);
                    // Prefer line after "ValidationError" or "UserError"
                    let found='';
                    for(let i=0;i<lines.length;i++){
                        if(lines[i].match(/ValidationError|UserError/i)&&lines[i+1]){
                            found=lines[i+1]; break;
                        }
                    }
                    if(!found) found=lines[lines.length-1]||lines[0]||raw;
                    msg=found.replace(/^[^:]+Error:\s*/i,'').trim()||raw.trim()||msg;
                }
            }catch(_){}
            this.form.errorMsg=msg;
        }finally{
            this.state.saving=false;
        }
    }
    async deleteModal(){
        if(!confirm("¿Eliminar este turno?"))return;
        await this.orm.call("nombramientos.calendario","delete_turno",[this.form.turno_id]);
        this.state.modal=null; await this.loadData();
    }
}

registry.category("actions").add("nombramientos_calendario",CalendarioNombramientos);
