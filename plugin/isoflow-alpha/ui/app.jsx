import React, {useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Isoflow} from 'isoflow';
import {convertGns3} from './gns3-adapter.js';

function App({demo}) {
    const [doc, setDoc] = useState(demo);
    const [filename, setFilename] = useState('ospf-multi-area.gns3');
    const [error, setError] = useState('');
    const [selected, setSelected] = useState('');
    const result = useMemo(() => {
        try { return {data: convertGns3(doc)}; }
        catch (e) { return {error: e.message}; }
    }, [doc]);
    const data = result.data;
    const node = data?.source.nodes.get(selected);
    const props = node?.properties || {};

    const readFile = async event => {
        const file = event.target.files?.[0];
        if (!file) return;
        if (file.size > 5 * 1024 * 1024) {
            setError('Файл больше 5 МБ');
            return;
        }
        try {
            const parsed = JSON.parse(await file.text());
            convertGns3(parsed);
            setDoc(parsed);
            setFilename(file.name);
            setSelected('');
            setError('');
        } catch (e) {
            setError(`Не удалось открыть файл: ${e.message}`);
        }
        event.target.value = '';
    };

    return <div className="alpha-shell">
        <div className="alpha-top">
            <div><span className="alpha-eyebrow">ISOflow / GNS3 · эксперимент</span><h2>{data?.model.title || 'Топология'}</h2>
                <p>Откройте свой .gns3 или изучите пример. Файл остаётся в браузере.</p></div>
            <label className="alpha-upload">Открыть .gns3<input type="file" accept=".gns3,.json,application/json" onChange={readFile}/></label>
        </div>
        {(error || result.error) && <p className="alpha-error" role="alert">{error || result.error}</p>}
        {data && <div className="alpha-body">
            <div className="alpha-canvas" aria-label="Схема топологии">
                <Isoflow key={filename} initialData={data.model} editorMode="EXPLORABLE_READONLY"
                    mainMenuOptions={[]} renderer={{showGrid: true, backgroundColor: '#eef4fa'}}/>
            </div>
            <aside className="alpha-side">
                <div className="alpha-stats"><b>{data.stats.nodes}</b> устройств <b>{data.stats.links}</b> связей</div>
                <div className="alpha-file">{filename}</div>
                {data.stats.skippedLinks > 0 && <p className="alpha-note">Пропущено связей: {data.stats.skippedLinks}</p>}
                {data.stats.drawings > 0 && <p className="alpha-note">Рисунки GNS3: {data.stats.drawings} (пока без отображения)</p>}
                <h3>Устройства</h3>
                <div className="alpha-list">{data.model.items.map(item =>
                    <button type="button" key={item.id} className={selected === item.id ? 'active' : ''}
                        onClick={() => setSelected(item.id)}><span>{item.name}</span><small>{item.description || item.icon}</small></button>)}</div>
                {node && <div className="alpha-detail"><h3>{node.name}</h3>
                    <dl><dt>ID GNS3</dt><dd>{node.node_id}</dd><dt>Тип</dt><dd>{node.node_type || '—'}</dd>
                        <dt>vCPU</dt><dd>{props.cpus ?? '—'}</dd><dt>RAM</dt><dd>{props.ram ? `${props.ram} МБ` : '—'}</dd>
                        <dt>Видеокарта</dt><dd>Нет данных в .gns3</dd></dl>
                    <h4>Соединения</h4><ul>{[...data.source.links.values()].filter(l => l.a === selected || l.b === selected)
                        .map(l => <li key={l.id}>{l.a === selected ? l.aLabel : l.bLabel} → {data.source.nodes.get(l.a === selected ? l.b : l.a)?.name}</li>)}</ul>
                </div>}
            </aside>
        </div>}
    </div>;
}

export function init(options) {
    const root = document.getElementById('gns3isoflow-alpha-root');
    if (!root) throw new Error('Isoflow alpha root not found');
    createRoot(root).render(<App demo={options.demo}/>);
}
