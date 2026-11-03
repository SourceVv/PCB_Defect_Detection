import html
import json
import os
from pathlib import Path
import cv2
import numpy as np
import ipywidgets as w
from IPython.display import display
from .appearance import build, sync, render_result
from .general import GeneralSession
from .legacy import file_from_widget, jpeg

class InventoryUI:

    def __init__(self, session):
        self.session = session
        self.busy = False
        self.reference = w.FileUpload(accept='.png,.jpg,.jpeg', multiple=False, description='正常参考照片')
        self.target = w.FileUpload(accept='.png,.jpg,.jpeg', multiple=False, description='待测照片')
        self.register = w.Button(description='识别参考器件', button_style='info')
        self.detect = w.Button(description='开始检查', button_style='primary', disabled=True)
        self.note = w.HTML()
        self.preview = w.Image(format='jpeg', layout=w.Layout(max_width='100%', display='none'))
        self.select = w.Dropdown(description='器件', layout=w.Layout(width='400px'))
        self.name = w.Text(description='位号')
        self.box = w.Text(description='封装区域', placeholder='x0,y0,x1,y1', layout=w.Layout(width='400px'))
        self.population = w.Checkbox(description='参考位置已装器件', value=True)
        self.enabled = w.Checkbox(description='参与检测', value=True)
        self.apply = w.Button(description='保存该器件')
        self.add = w.Button(description='补充漏识别器件')
        self.save = w.Button(description='保存参考档案')
        self.profiles = w.Dropdown(description='已存参考', layout=w.Layout(width='440px'))
        self.load = w.Button(description='载入')
        self.result = w.HTML()
        self.target_image = w.Image(format='jpeg', layout=w.Layout(max_width='100%', display='none'))
        self.compare_select = w.Dropdown(description='局部对照', layout=w.Layout(width='480px'))
        self.compare_reference = w.Image(format='jpeg', layout=w.Layout(max_width='100%', display='none'))
        self.compare_target = w.Image(format='jpeg', layout=w.Layout(max_width='100%', display='none'))
        self.compare_note = w.HTML()
        pair = w.HBox([w.VBox([w.HTML('<b>正常参考</b>'), self.compare_reference], layout=w.Layout(width='48%')), w.VBox([w.HTML('<b>对齐后的待测器件</b>'), self.compare_target], layout=w.Layout(width='48%'))], layout=w.Layout(width='100%', flex_flow='row wrap'))
        self.compare_panel = w.VBox([w.HTML('<h3>单器件放大对比</h3>'), self.compare_select, self.compare_note, pair], layout=w.Layout(display='none', width='100%'))
        self.export = w.Button(description='保存检测结果', disabled=True)
        self.local = w.Image(format='jpeg', layout=w.Layout(max_width='480px', display='none'))
        self.tips = w.HTML('<div style="padding:14px;background:#edf5ff;line-height:1.7">上传同型号、同一面的完整平面照片。允许位置、缩放和图片尺寸不同。避免模糊、遮挡和强反光；参考板应已确认正常。最小元件短边建议至少15像素，位号文字应清晰。芯片存在检查使用封装主体；方向检查需要可辨识的外观方向依据。</div>')
        build(self)
        self.register.on_click(self.on_register)
        self.detect.on_click(self.on_detect)
        self.apply.on_click(self.on_apply)
        self.add.on_click(self.on_add)
        self.save.on_click(self.on_save)
        self.load.on_click(self.on_load)
        self.export.on_click(self.on_export)
        self.select.observe(self.on_select, names='value')
        self.compare_select.observe(self.show_comparison, names='value')
        self.reference.observe(self.reference_changed, names='value')
        self.target.observe(self.target_changed, names='value')
        self.refresh_profiles()
        sync(self)

    def set_image(self, widget, data=b''):
        widget.layout.display = 'none'
        widget.value = data
        if data:
            widget.layout.display = ''

    def clear_comparison(self):
        self.compare_panel.layout.display = 'none'
        self.compare_select.options = []
        self.compare_note.value = ''
        self.set_image(self.compare_reference)
        self.set_image(self.compare_target)

    def populate_comparison(self):
        self.clear_comparison()
        if self.session.last is None:
            return
        items = self.session.last['items']
        self.compare_select.options = [(item['refdes'], index) for index, item in enumerate(items)]
        if items and self.compare_select.value is None:
            self.compare_select.value = 0
        self.compare_panel.layout.display = ''
        self.show_comparison()

    def show_comparison(self, change=None):
        result = self.session.last
        index = self.compare_select.value
        if result is None or index is None:
            return
        item = result['items'][index]
        values = [item['refdes'], '存在：' + item['presence'], '歪斜：' + item['tilt'], '方向：' + item['orientation'], item['reason']]
        self.compare_note.value = '<p>' + html.escape('；'.join(values)) + '</p>'
        box = item['box']
        if box is None:
            self.set_image(self.compare_reference)
            self.set_image(self.compare_target)
            self.compare_note.value += '<p>这个位号尚未登记封装区域，无法显示对应局部图。</p>'
            return
        x0, y0, x1, y1 = box
        height, width = result['aligned'].shape[:2]
        padding = max(6, round(min(x1 - x0, y1 - y0) * 0.2))
        left, top, right, bottom = (max(0, x0 - padding), max(0, y0 - padding), min(width, x1 + padding), min(height, y1 + padding))
        for widget, image in ((self.compare_reference, self.session.engine.image), (self.compare_target, result['aligned'])):
            crop = image[top:bottom, left:right].copy()
            cv2.rectangle(crop, (x0 - left, y0 - top), (x1 - left - 1, y1 - top - 1), (0, 150, 240), 1)
            scale = min(460 / crop.shape[1], 300 / crop.shape[0], 6)
            enlarged = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST if scale > 1 else cv2.INTER_AREA)
            self.set_image(widget, jpeg(enlarged, 460))

    def message(self, text):
        self.note.value = '<div class="pcb-notice" role="status">' + html.escape(str(text)) + '</div>'

    def run(self, function):
        if self.busy:
            return
        self.busy = True
        controls = [self.register, self.detect, self.apply, self.add, self.save, self.load, self.reference, self.target, self.export, self.select, self.name, self.box, self.population, self.enabled, self.profiles, self.compare_select]
        for control in controls:
            control.disabled = True
        try:
            function()
        except Exception as exc:
            self.message(str(exc))
        finally:
            self.busy = False
            for control in controls:
                control.disabled = False
            self.detect.disabled = self.session.engine is None
            self.export.disabled = self.session.last is None
            sync(self)

    def reference_changed(self, change):
        if not self.busy:
            self.clear_comparison()
            for widget in (self.preview, self.local, self.target_image):
                self.set_image(widget)
            self.select.options = []
            self.detect.disabled = True
            self.session.engine = None
            self.session.last = None
            self.result.value = ''
            self.export.disabled = True
            self.message('已更换参考，请重新识别参考器件。')
            sync(self)

    def target_changed(self, change):
        if not self.busy:
            self.clear_comparison()
            self.set_image(self.target_image)
            self.session.last = None
            self.result.value = ''
            self.export.disabled = True
            sync(self)

    def refresh_profiles(self):
        paths = sorted((self.session.root / 'reference_profiles').glob('*/inventory.json'))
        self.profiles.options = [(p.parent.name, str(p.parent)) for p in paths]

    def refresh(self):
        self.clear_comparison()
        rows = self.session.rows
        self.select.options = [(r['refdes'] + (' ✓' if r['reviewed'] else ' · 待核对'), i) for i, r in enumerate(rows)]
        if rows and self.select.value is None:
            self.select.value = 0
        image = self.session.engine.image.copy()
        for row in rows:
            if row['box']:
                x0, y0, x1, y1 = row['box']
                cv2.rectangle(image, (x0, y0), (x1, y1), (0, 160, 255), 1)
                cv2.putText(image, row['refdes'], (x0, max(15, y0 - 3)), 0, 0.45, (0, 0, 220), 1)
        for x in range(0, image.shape[1], 100):
            cv2.putText(image, str(x), (x, 18), 0, 0.4, (0, 0, 220), 1)
        for y in range(100, image.shape[0], 100):
            cv2.putText(image, str(y), (0, y), 0, 0.4, (0, 0, 220), 1)
        self.set_image(self.preview, jpeg(image, 1200))
        self.on_select()
        if self.session.last is not None:
            self.populate_comparison()

    def on_register(self, button):

        def action():
            name, data = file_from_widget(self.reference.value)
            self.message('正在读取四个方向的位号并寻找邻近封装，请等待。')
            rows = self.session.register(name, data)
            self.refresh()
            reviewed = sum((row['reviewed'] for row in rows))
            self.message(f'参考清单共 {len(rows)} 个位置，已登记 {reviewed} 个，待核对 {len(rows) - reviewed} 个。检测会列出全部位置。')
        self.run(action)

    def on_select(self, change=None):
        if self.select.value is None:
            self.set_image(self.local)
            return
        row = self.session.rows[self.select.value]
        self.name.value = row['refdes']
        self.box.value = ','.join(map(str, row['box'])) if row['box'] else ''
        self.population.value = row['populated'] is not False
        self.enabled.value = row['enabled']
        if row['box']:
            x0, y0, x1, y1 = row['box']
            crop = self.session.engine.image[max(0, y0 - 20):y1 + 20, max(0, x0 - 20):x1 + 20]
            self.set_image(self.local, jpeg(cv2.resize(crop, None, fx=3, fy=3), 480))
        else:
            self.set_image(self.local)

    def on_add(self, button):
        self.name.value = ''
        self.box.value = ''
        self.select.value = None
        self.message('输入漏识别位号和参考图上的封装坐标，点击保存该器件。')

    def on_apply(self, button):

        def action():
            index = self.select.value if self.select.value is not None else len(self.session.rows)
            box = [int(v.strip()) for v in self.box.value.replace('，', ',').split(',')]
            if len(box) != 4:
                raise ValueError('请输入四个区域坐标')
            self.session.update(index, self.name.value, box, self.population.value, self.enabled.value)
            self.result.value = ''
            self.refresh()
            self.select.value = index
            self.message('已保存器件信息。')
        self.run(action)

    def on_save(self, button):

        def action():
            self.message('参考档案已保存：' + str(self.session.save_profile()))
            self.refresh_profiles()
        self.run(action)

    def on_load(self, button):

        def action():
            if not self.profiles.value:
                raise ValueError('没有已保存的参考档案')
            self.session.load_profile(self.profiles.value)
            self.refresh()
            self.result.value = ''
            self.message('已恢复参考清单，可继续检查。')
        self.run(action)

    def on_detect(self, button):

        def action():
            self.clear_comparison()
            self.session.last = None
            self.result.value = ''
            self.set_image(self.target_image)
            name, data = file_from_widget(self.target.value)
            result = self.session.inspect(data)
            annotated = result['original'].copy()
            inverse = np.linalg.inv(np.array(result['alignment']['original_target_to_reference']))
            for item in result['items']:
                if item['box'] is None or not item['enabled']:
                    continue
                x0, y0, x1, y1 = item['box']
                points = cv2.perspectiveTransform(np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]), inverse)[0]
                defect = any((word in str(item.get(key, '')) for word in ('缺件', '焊反', '疑似歪斜') for key in ('presence', 'orientation', 'tilt')))
                color = (0, 0, 220) if defect else (0, 160, 0) if item['presence'] == '存在' else (0, 160, 230)
                cv2.polylines(annotated, [np.rint(points).astype(np.int32)], True, color, 1)
                x, y = points.min(axis=0).astype(int)
                cv2.putText(annotated, item['refdes'], (max(0, x), max(12, y - 3)), 0, 0.35, color, 1)
            self.set_image(self.target_image, jpeg(annotated, 1200))
            self.result.value = render_result(name, result)
            self.current_filename = name
            self.populate_comparison()
            self.message('检查完成。待确认项目需要复核，不计为正常。')
        self.run(action)

    def on_export(self, button):

        def action():
            from uuid import uuid4
            import csv
            if self.session.last is None:
                raise ValueError('请先完成检测')
            result = self.session.last
            path = self.session.root / 'inspection_results' / uuid4().hex
            path.mkdir(parents=True)
            record = {k: v for k, v in result.items() if k not in ('aligned', 'original', 'operator')}
            if result['operator'] is not None:
                operator = result['operator']
                np.save(path / 'actual_fpga_mask.npy', operator['mask'], allow_pickle=False)
                if 'calibrated_mask' in operator:
                    np.save(path / 'calibrated_fpga_mask.npy', operator['calibrated_mask'], allow_pickle=False)
                record['operator_frame_id'] = operator['frame_id']
            (path / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            if not cv2.imwrite(str(path / 'aligned.png'), result['aligned']):
                raise OSError('图像保存失败')
            with (path / 'result.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['位号', '存在检查', '电容歪斜', '芯片方向', '说明'])
                for item in result['items']:
                    writer.writerow([item[k] for k in ('refdes', 'presence', 'tilt', 'orientation', 'reason')])
            self.message('结果已保存：' + str(path))
        self.run(action)

def launch(previous=None, board=False, existing_driver=None):
    if previous is not None:
        if previous.busy:
            raise RuntimeError('请等待当前操作结束')
        previous.widget.close()
        session = previous.session
    else:
        root = Path(os.environ.get('PCB_PROJECT_ROOT', str(Path.cwd())))
        if board:
            from .backend import BoardThreshold
            session = GeneralSession(root, BoardThreshold(root, existing_driver))
        else:
            session = GeneralSession(root)
    app = InventoryUI(session)
    if session.engine is not None:
        app.refresh()
        app.detect.disabled = False
    if previous is not None and session.last is not None:
        app.current_filename = previous.current_filename
        app.set_image(app.target_image, bytes(previous.target_image.value))
        app.result.value = render_result(app.current_filename, session.last)
    sync(app)
    display(app.widget)
    return app
