import base64
import html
import ipywidgets as w
CSS = '<style>\n.pcb-app{--ink:#173353;--blue:#1757dd;--line:#dce9f0;--muted:#657f95;background:#fff;color:var(--ink);font-family:"Microsoft YaHei","PingFang SC",sans-serif;font-size:14px;line-height:1.65;width:100%;max-width:1600px;margin:12px auto;border:1px solid var(--line);box-sizing:border-box;}\n.pcb-app *{box-sizing:border-box}.pcb-app .widget-html-content{width:100%;line-height:inherit}.pcb-app .widget-html{margin:0;min-width:0}.pcb-app .widget-box{min-width:0;flex-shrink:0}.pcb-app h1,.pcb-app h2,.pcb-app h3,.pcb-app p{margin:0}.pcb-app h1{font-size:46px;line-height:1.3;letter-spacing:-1.5px;font-weight:600}.pcb-app h2{font-size:27px;font-weight:600;letter-spacing:-.6px}.pcb-app h3{font-size:18px;font-weight:600}.pcb-app .pcb-eyebrow{font-size:10px;letter-spacing:3px;color:#2566be;font-weight:600;margin-bottom:20px}.pcb-app .pcb-muted{color:var(--muted);font-size:12px}.pcb-app .pcb-header{padding:20px 32px;border-bottom:1px solid var(--line);align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}.pcb-app .pcb-brand{font-size:15px;letter-spacing:1px;font-weight:600}.pcb-app .pcb-brand i{display:inline-block;width:19px;height:19px;margin-right:10px;background:var(--blue);border-radius:50% 0 50% 50%;vertical-align:middle}.pcb-app .pcb-brand small{margin-left:12px;color:#7890a3;font-size:10px;letter-spacing:2px;font-weight:400}.pcb-app .pcb-nav{gap:6px;flex-wrap:wrap}.pcb-app .widget-button{height:38px;min-height:38px;width:auto;min-width:94px;margin:0;padding:0 17px;box-shadow:none!important;border:1px solid #b9d1e6;border-radius:3px;background:#fff;color:var(--ink);font-size:12px;font-weight:500;white-space:nowrap}.pcb-app .widget-button:hover{background:#eef6ff;border-color:#6299e2}.pcb-app .widget-button:focus-visible{outline:2px solid #2563eb;outline-offset:3px}.pcb-app .widget-button:disabled{opacity:.45;cursor:not-allowed}.pcb-app .pcb-primary{background:var(--blue);color:white;border-color:var(--blue)}.pcb-app .pcb-primary:hover{background:#1047bf;color:white}.pcb-app .pcb-nav .widget-button{border:0;border-bottom:2px solid transparent;border-radius:0;color:#6a7e93;background:transparent;min-width:88px}.pcb-app .pcb-nav .pcb-active{color:var(--blue);border-bottom-color:var(--blue)}\n.pcb-app .pcb-body{padding:30px 32px 34px;width:100%}.pcb-app .pcb-page-title{margin:0 0 26px;border-bottom:1px solid var(--line);padding-bottom:20px;position:relative}.pcb-app .pcb-page-title:after{content:"";position:absolute;right:0;bottom:0;width:90px;height:45px;background:#e8f7fb;border-radius:90px 90px 0 0;border:1px solid #a4dce9}.pcb-app .pcb-page-title .pcb-eyebrow{margin-bottom:8px}.pcb-app .pcb-page-title p{margin-top:6px;color:var(--muted);font-size:12px}.pcb-app .pcb-hero{position:relative;min-height:500px;overflow:hidden;padding:62px 0 48px}.pcb-app .pcb-hero-art{position:absolute;right:-22px;top:-15px;width:65%;height:100%;object-fit:contain;object-position:right center;pointer-events:none}.pcb-app .pcb-hero-copy{position:relative;width:48%;z-index:1;padding-top:30px}.pcb-app .pcb-hero-copy p{margin-top:24px;line-height:2;color:#607c95;font-size:14px}.pcb-app .pcb-hero-copy .pcb-eyebrow{margin-bottom:24px}.pcb-app .pcb-home-actions{gap:12px;flex-wrap:wrap;margin-top:-68px;position:relative;z-index:2;margin-bottom:64px}.pcb-app .pcb-steps{display:grid;grid-template-columns:repeat(3,1fr);gap:28px;border-top:1px solid var(--line);padding-top:26px}.pcb-app .pcb-step small{font-size:11px;color:#4d86d4;letter-spacing:2px}.pcb-app .pcb-step b{display:block;font-weight:500;font-size:17px;margin:8px 0}.pcb-app .pcb-step p{font-size:12px;color:var(--muted)}\n.pcb-app .pcb-row{gap:12px;align-items:center;flex-wrap:wrap;width:100%;margin:12px 0}.pcb-app .pcb-section-head{padding:8px 0 14px}.pcb-app .pcb-section-head small{font-size:10px;letter-spacing:2px;color:#4d86d4;display:block;margin-bottom:5px}.pcb-app .pcb-section-head p{font-size:12px;color:var(--muted);margin-top:6px}.pcb-app .pcb-tips{border-left:2px solid #75bbdf;padding:10px 16px;margin:4px 0 16px;color:#58738c;font-size:12px;background:#f6fbfe}.pcb-app .pcb-two-col{gap:28px;align-items:flex-start;flex-wrap:wrap;width:100%;margin:16px 0 24px}.pcb-app .pcb-canvas{flex:none;width:100%;min-width:0;background:#f5f9fb;padding:16px;border:1px solid #e1ecf1}.pcb-app .pcb-two-col>.pcb-canvas{flex:2 1 450px;width:auto}.pcb-app .pcb-editor{flex:1 1 280px;width:auto;min-width:0;padding:0 0 0 8px}.pcb-app .pcb-empty{min-height:230px;display:flex;align-items:center;justify-content:center;flex-direction:column;color:#8a9dac;text-align:center;padding:30px}.pcb-app .pcb-empty span{width:54px;height:54px;border:1px solid #8bbad4;border-radius:50% 50% 0 50%;margin-bottom:18px;position:relative}.pcb-app .pcb-empty span:after{content:"";position:absolute;width:20px;height:20px;border:1px solid #8bbad4;right:-12px;bottom:4px}.pcb-app .pcb-empty b{color:#536f88;font-weight:500;font-size:15px;margin-bottom:7px}.pcb-app .pcb-editor .widget-label{color:#58738b;font-size:12px;text-align:left;width:90px!important;min-width:90px}.pcb-app .widget-text,.pcb-app .widget-dropdown{max-width:100%;margin:7px 0;min-width:0;height:35px}.pcb-app .widget-text input,.pcb-app .widget-dropdown select{color:var(--ink);background:white;border:1px solid #cbdde9;border-radius:2px;font-size:12px;min-width:0;height:34px}.pcb-app .widget-checkbox{width:100%;margin:3px 0}.pcb-app .widget-checkbox .widget-label{width:auto!important}.pcb-app .widget-checkbox label{font-size:12px;color:#58738b}.pcb-app .widget-image{object-fit:contain;max-width:100%;height:auto;margin:auto}.pcb-app .pcb-target-section{border-top:1px solid var(--line);padding-top:24px;width:100%;margin-top:12px}.pcb-app .pcb-notice{padding:12px 32px;background:#f0f7fc;border-bottom:1px solid #d9e9f4;color:#3f6587;font-size:12px;overflow-wrap:anywhere}.pcb-app .pcb-summary{display:grid;grid-template-columns:repeat(4,1fr);padding:24px 0;margin:8px 0 14px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);gap:16px}.pcb-app .pcb-metric{border-right:1px solid var(--line);padding:0 18px}.pcb-app .pcb-metric:first-child{padding-left:0}.pcb-app .pcb-metric:last-child{border:0}.pcb-app .pcb-metric strong{font-size:32px;font-weight:500;color:#205bb7;display:block;line-height:1.3}.pcb-app .pcb-metric span{font-size:11px;color:#667f94}.pcb-app .pcb-reading{font-size:12px;color:#667f94;line-height:2;padding:8px 0 20px}.pcb-app .pcb-table-wrap{width:100%;overflow:auto}.pcb-app table{width:100%;border-collapse:collapse;font-size:12px;text-align:left;margin:18px 0!important;color:var(--ink)}.pcb-app th{background:#f1f7fc;color:#536f88;font-size:11px;font-weight:500;white-space:nowrap}.pcb-app th,.pcb-app td{padding:13px 12px!important;border-bottom:1px solid #e3ecf3!important;text-align:left!important;vertical-align:top}.pcb-app td:first-child{font-weight:600;color:#235fae}.pcb-app td:last-child{min-width:210px}.pcb-app .pcb-status{display:inline-block;padding:2px 7px;border-radius:2px;font-size:11px;white-space:nowrap;background:#edf4fa;color:#426c93}.pcb-app .pcb-status.pending{background:#faf2de;color:#886a2f}.pcb-app .pcb-status.defect{background:#ffede8;color:#b85942}.pcb-app .pcb-status.neutral{background:#f0f3f5;color:#7a8b99}.pcb-app .pcb-footer{display:flex;justify-content:space-between;gap:16px;border-top:1px solid var(--line);padding:15px 32px;color:#92a3b1;font-size:10px;letter-spacing:1px}.pcb-app .pcb-compare{padding:24px 0;border-top:1px solid var(--line)}.pcb-app .pcb-compare h3{margin-bottom:12px}.pcb-app .pcb-compare .widget-hbox{gap:20px}.pcb-app .pcb-compare .widget-hbox>.widget-vbox{background:#f5f9fb;padding:16px;flex:1 1 240px;width:auto!important}.pcb-app .pcb-compare b{font-size:12px;font-weight:500;color:#6a8297}.pcb-app .pcb-compare p{font-size:12px;color:#627d94;margin:8px 0 16px}.pcb-app .pcb-result-file{overflow-wrap:anywhere;color:#527395;font-size:12px;margin:8px 0}.pcb-app .pcb-results-empty{padding:50px 0;color:#8a9dac;text-align:center}\n@media(max-width:1100px){.pcb-app .pcb-header,.pcb-app .pcb-body{padding:20px}.pcb-app h1{font-size:33px}.pcb-app .pcb-hero{min-height:490px;padding-top:30px}.pcb-app .pcb-hero-copy{width:90%}.pcb-app .pcb-hero-art{opacity:.35;width:90%;top:90px}.pcb-app .pcb-summary{grid-template-columns:repeat(2,1fr)}.pcb-app .pcb-steps{gap:15px}.pcb-app .pcb-brand small{display:none}.pcb-app .pcb-footer{padding:15px 20px}.pcb-app .pcb-page-title:after{display:none}}\n</style>'

def artwork():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="700" viewBox="0 0 900 700">\n    <defs><filter id="grain"><feTurbulence type="fractalNoise" baseFrequency=".6" numOctaves="3" seed="19" result="noise"/><feColorMatrix in="noise" type="saturate" values="0"/><feBlend in="SourceGraphic" mode="soft-light"/><feComposite in2="SourceGraphic" operator="in"/></filter><clipPath id="circle"><circle cx="540" cy="335" r="335"/></clipPath><pattern id="stripes" width="12" height="12" patternUnits="userSpaceOnUse"><path d="M0 1H12" stroke="#b6ecf4" stroke-width="4"/></pattern></defs>\n    <g clip-path="url(#circle)" filter="url(#grain)">\n    <path fill="#4095ef" d="M120 0H900V260L150 530Z"/><path fill="#1654dd" d="M470 -30H930V760H700L410 390Z"/>\n    <path fill="#9ee1ee" d="M160 210Q540 40 700 330T890 680L480 740 190 430Z"/>\n    <circle cx="440" cy="210" r="165" fill="#d5f3f7" fill-opacity=".8"/>\n    <path d="M250 80A260 260 0 1 1 260 600L330 500A145 145 0 1 0 330 190Z" fill="#236be4" fill-opacity=".75"/>\n    <path d="M190 380L800 90 720 360 470 595Z" fill="#e9faff" fill-opacity=".8"/>\n    <path d="M540 30V180A75 75 0 0 0 690 180V30Z" fill="#74d5ed"/>\n    <path d="M670 360A165 165 0 0 1 835 525H670Z" fill="#1352c8"/>\n    <circle cx="320" cy="540" r="95" fill="#80c9f3" fill-opacity=".65"/>\n    <path fill="url(#stripes)" d="M120 280H410V405H120ZM710 530H930V690H710Z"/>\n    <path d="M370 590L710 420 835 690Z" fill="#dcf7fa" fill-opacity=".8"/>\n    <path d="M555 400L680 400 680 465 555 465Z" fill="#327ed7" fill-opacity=".5"/>\n    </g>\n    <g fill="none" stroke="#66bfd9" stroke-width="1.5" opacity=".85">\n    <circle cx="540" cy="335" r="335"/><circle cx="440" cy="210" r="165"/><path d="M130 360H445L510 425H720M240 60L770 670M220 640L825 190M110 470H380L460 550H780M565 0V285L675 395V650"/>\n    <circle cx="425" cy="350" r="48"/><circle cx="705" cy="255" r="30"/>\n    <rect x="480" y="305" width="125" height="96" rx="4"/>\n    <path d="M495 290V305M515 290V305M535 290V305M555 290V305M575 290V305M595 290V305M495 401V416M515 401V416M535 401V416M555 401V416M575 401V416M595 401V416M465 320H480M465 340H480M465 360H480M465 380H480M605 320H620M605 340H620M605 360H620M605 380H620"/>\n    </g><g fill="#f8fdff" stroke="#67c2dd" stroke-width="2"><circle cx="380" cy="470" r="13"/><circle cx="675" cy="395" r="12"/><circle cx="445" cy="360" r="9"/><circle cx="780" cy="550" r="11"/></g>\n    <g filter="url(#grain)"><path d="M750 420L790 450 785 497 756 480Z" fill="#f28c75"/><path d="M305 600L365 570 340 615Z" fill="#f18177"/><path d="M775 375A40 40 0 0 1 815 415H775Z" fill="#f5d98d"/></g></svg>'
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()

def H(text):
    return w.HTML(text)

def box(children, cls='', horizontal=False):
    obj = (w.HBox if horizontal else w.VBox)(children, layout=w.Layout(width='100%'))
    if cls:
        obj.add_class(cls)
    return obj

def heading(n, title, subtitle=''):
    return H('<div class="pcb-section-head"><small>' + n + '</small><h3>' + title + '</h3><p>' + subtitle + '</p></div>')

def empty(title, subtitle):
    return H('<div class="pcb-empty"><span></span><b>' + title + '</b><p>' + subtitle + '</p></div>')

def build(a):
    a.current_filename = ''
    a.reference_empty = empty('登记一张正常参考照片', '参考器件框与位号将在这里显示')
    a.target_empty = empty('尚未检查', '上传单张待测照片，完成检查后查看标注结果')
    a.analysis_empty = empty('暂无检测结果', '完成一次离线检查后，在这里阅读结果')
    a.summary = w.HTML()
    a.inspection_summary = w.HTML()
    a.analysis_image = w.Image(format='jpeg', layout=w.Layout(max_width='100%', display='none'))
    a.file_label = w.HTML()
    for c in (a.select, a.name, a.box, a.profiles, a.compare_select):
        c.layout.width = '100%'
        c.style.description_width = '90px'
    a.profiles.layout.width = 'auto'
    a.profiles.layout.flex = '1 1 320px'
    for c in (a.register, a.detect):
        c.add_class('pcb-primary')
    a.local.layout.max_width = '100%'
    a.analysis_image.layout.max_height = '520px'
    a.target_image.layout.max_height = '600px'
    a.compare_panel.add_class('pcb-compare')
    a.tips.value = '<div class="pcb-tips">同型号、同一面，上传完整平面照片；允许位置、缩放和尺寸不同。避免模糊、遮挡和强反光。参考板须已确认正常，最小元件短边建议至少15像素，位号与芯片方向依据应清晰。</div>'
    nav = [w.Button(description=t) for t in ('首页', '离线检测', '结果分析')]
    a.nav = nav
    hero = H('<div class="pcb-hero"><img class="pcb-hero-art" alt="蓝色颗粒几何与PCB电路构成" src="' + artwork() + '"><div class="pcb-hero-copy"><div class="pcb-eyebrow">PCB / OPTICAL INSPECTION</div><h1>PCB 全参考<br>清单检测</h1><p>登记正常参考 · 核对器件清单 · 检查待测照片</p></div></div>')
    start = w.Button(description='登记正常参考  →')
    start.add_class('pcb-primary')
    load = w.Button(description='载入参考档案')
    home = box([hero, box([start, load], 'pcb-home-actions', True), H('<div class="pcb-steps"><div class="pcb-step"><small>01 / REFERENCE</small><b>登记正常参考</b><p>识别位号，建立参考清单。</p></div><div class="pcb-step"><small>02 / REVIEW</small><b>核对器件清单</b><p>确认封装区域、装件与参考空位。</p></div><div class="pcb-step"><small>03 / INSPECT</small><b>离线检查</b><p>分别查看存在、芯片方向与电容歪斜。</p></div></div>')], 'pcb-body')

    def title(index, name, sub):
        return H('<div class="pcb-page-title"><div class="pcb-eyebrow">' + index + '</div><h2>' + name + '</h2><p>' + sub + '</p></div>')
    reference_canvas = box([a.reference_empty, a.preview], 'pcb-canvas')
    editor = box([heading('REFERENCE INVENTORY', '核对参考清单', '选择位号核对封装区域；空焊盘取消“已装器件”。'), a.select, a.local, a.name, a.box, a.population, a.enabled, box([a.apply, a.add], 'pcb-row', True), a.save], 'pcb-editor')
    a.to_results = w.Button(description='查看结果分析  →')
    target_section = box([heading('02 / INSPECTION', '检查待测板', '使用当前参考清单，检查单张待测照片。'), box([a.target, a.detect, a.to_results], 'pcb-row', True), a.inspection_summary, box([a.target_empty, a.target_image], 'pcb-canvas')], 'pcb-target-section')
    inspect = box([title('WORKSPACE / 01', '离线检测', '先核对参考，再检查待测照片。'), heading('01 / REFERENCE', '登记正常参考'), box([a.reference, a.register], 'pcb-row', True), a.tips, box([a.profiles, a.load], 'pcb-row', True), box([reference_canvas, editor], 'pcb-two-col', True), target_section], 'pcb-body')
    analysis = box([title('RESULT / 02', '结果分析', '当前检测结果 · 按位号逐项复核'), a.file_label, a.summary, H('<div class="pcb-reading">已计算位置仍可能包含待确认结论，各项不可直接相加。<br>存在、方向与歪斜分别判断；待确认项目需复核，不计为正常。参考空焊盘不报缺件。</div>'), box([a.analysis_empty, a.analysis_image], 'pcb-canvas'), a.compare_panel, heading('COMPONENT RESULTS', '逐项检查结果'), a.result, box([a.export, H('<span class="pcb-muted">保存 CSV、JSON 与对齐图</span>')], 'pcb-row', True)], 'pcb-body')
    a.pages = [home, inspect, analysis]

    def navigate(index):
        for i, page in enumerate(a.pages):
            page.layout.display = '' if i == index else 'none'
            nav[i].remove_class('pcb-active')
        nav[index].add_class('pcb-active')
    a.navigate = navigate
    for i, b in enumerate(nav):
        b.on_click(lambda _, i=i: navigate(i))
    start.on_click(lambda _: navigate(1))
    load.on_click(lambda _: navigate(1))
    a.to_results.on_click(lambda _: navigate(2))
    header = box([H('<div class="pcb-brand"><i></i>PCB / VISION <small>全参考清单检测</small></div>'), box(nav, 'pcb-nav', True)], 'pcb-header', True)
    header.children[1].layout.width = 'auto'
    a.widget = box([H(CSS), header, a.note, *a.pages, H('<div class="pcb-footer"><span>PCB / OPTICAL INSPECTION</span><span>参考登记 → 清单核对 → 离线检查</span></div>')], 'pcb-app')
    navigate(0)

def render_result(name, result):
    rows = []
    for item in result['items']:
        cells = []
        for key in ('refdes', 'presence', 'tilt', 'orientation', 'reason'):
            value = str(item[key])
            safe = html.escape(value)
            if key in ('presence', 'tilt', 'orientation'):
                state = 'pending' if '待确认' in value else 'defect' if any((t in value for t in ('缺件', '焊反', '疑似歪斜'))) or value == '歪斜' else 'neutral' if value in ('不适用', '未启用', '参考为空焊盘', '未标定') else ''
                safe = '<span class="pcb-status ' + state + '">' + safe + '</span>'
            cells.append('<td>' + safe + '</td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    return '<div class="pcb-table-wrap"><table><thead><tr><th>位号</th><th>存在检查</th><th>电容歪斜</th><th>芯片方向</th><th>说明</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'

def sync(a):
    result = a.session.last
    a.reference_empty.layout.display = 'none' if a.session.engine is not None else ''
    a.note.layout.display = '' if a.note.value else 'none'
    a.apply.disabled = a.add.disabled = a.save.disabled = a.session.engine is None or a.busy
    a.load.disabled = not bool(a.profiles.options) or a.busy
    a.to_results.disabled = result is None
    a.export.disabled = result is None or a.busy
    a.detect.disabled = a.session.engine is None or a.busy
    if result is None:
        a.set_image(a.target_image)
        a.set_image(a.analysis_image)
        a.clear_comparison()
        a.current_filename = ''
        a.result.value = '<div class="pcb-results-empty">尚未检查 · 暂无逐项结果</div>'
    else:
        a.analysis_image.value = a.target_image.value
        a.analysis_image.layout.display = '' if a.target_image.value else 'none'
    a.target_empty.layout.display = 'none' if result is not None else ''
    a.analysis_empty.layout.display = 'none' if result is not None else ''
    a.file_label.value = '<div class="pcb-result-file">待测文件 / ' + html.escape(a.current_filename or '—') + '</div>'
    coverage = result['coverage'] if result is not None else {}
    a.summary.value = '<div class="pcb-summary">' + ''.join(('<div class="pcb-metric"><strong>' + str(coverage.get(k, '—')) + '</strong><span>' + label + '</span></div>' for k, label in [('total', '清单总数'), ('evaluated', '已计算位置'), ('reference_empty', '参考空位'), ('pending', '存在检查待确认')])) + '</div>'
    a.inspection_summary.value = a.summary.value if result is not None else ''
