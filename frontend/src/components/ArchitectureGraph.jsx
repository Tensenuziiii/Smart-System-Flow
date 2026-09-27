"use client";
import { useState, useCallback, useEffect } from 'react';
import ReactFlow, { Background, Controls, MiniMap, MarkerType } from 'reactflow';
import 'reactflow/dist/style.css';
import Editor from '@monaco-editor/react';
import { RefreshCw, X, ArrowDownRight, ArrowUpRight } from 'lucide-react';
import { apiUrl } from '../lib/api';

const clusterForPath = path => path.split('/')[0] || path;

export default function ArchitectureGraph({ repoId, token, grouped = false, onSelectionChange }) {
  const [rawNodes, setRawNodes] = useState([]);
  const [rawEdges, setRawEdges] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState('');
  const [selectedNode, setSelectedNode] = useState(null);
  const [fileIndex, setFileIndex] = useState({});
  const [expandedClusters, setExpandedClusters] = useState(new Set());

  const loadFileIndex = useCallback(async () => {
    const response = await fetch(apiUrl(`/v1/repositories/${repoId}/files?page_size=200`), {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await response.json();
    return Object.fromEntries((data.files || []).map(file => [file.id, file]));
  }, [repoId, token]);

  const fetchGraph = useCallback(async () => {
    const res = await fetch(apiUrl(`/v1/repositories/${repoId}/architecture`), {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    const files = await loadFileIndex();
    setFileIndex(files);
    
    // Layout heuristically (grid based on type)
    const typeX = { frontend: 100, api: 400, service: 700, db: 1000, external: 1300 };
    const counts = { frontend: 0, api: 0, service: 0, db: 0, external: 0 };
    
    setRawNodes(data.nodes.map(n => {
      const x = typeX[n.type] || 400;
      const y = (counts[n.type] || 0) * 80 + 100;
      counts[n.type] = (counts[n.type] || 0) + 1;
      
      return {
        id: n.id,
        position: { x, y },
        data: { label: `${n.label}\n(${n.type})`, metadata: n },
        style: {
          background: n.type === 'frontend' ? '#e0f2fe' : n.type === 'db' ? '#dcfce7' : '#f3f4f6',
          border: '1px solid #9ca3af',
          borderRadius: '8px',
          padding: '10px',
          width: 150
        }
      };
    }));

    setRawEdges(data.edges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      animated: true,
      label: e.relationship,
      markerEnd: { type: MarkerType.ArrowClosed, color: '#0f766e' },
      style: { stroke: '#0f766e', strokeWidth: e.relationship === 'calls' ? 2.5 : 1.5 }
    })));
  }, [loadFileIndex, repoId, token]);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  const clusterGroups = {};
  rawNodes.forEach(node => {
    const path = node.data.metadata?.metadata?.path || node.data.metadata?.label || 'root';
    const cluster = clusterForPath(path);
    if (!clusterGroups[cluster]) clusterGroups[cluster] = [];
    clusterGroups[cluster].push(node);
  });

  const clusterKeys = Object.keys(clusterGroups);
  const visibleNodes = grouped ? clusterKeys.flatMap((cluster, index) => {
    if (expandedClusters.has(cluster)) return clusterGroups[cluster];
    return [{
      id: `cluster::${cluster}`,
      position: { x: (index % 5) * 260 + 100, y: Math.floor(index / 5) * 130 + 100 },
      data: {
        cluster: true,
        clusterKey: cluster,
        label: `${cluster} (${clusterGroups[cluster].length} files)`,
        metadata: { label: cluster, path: cluster, language: 'mixed', file_count: clusterGroups[cluster].length },
      },
      style: {
        background: '#e6f5f1', border: '2px solid #7cbdb2', borderRadius: '8px',
        padding: '14px', width: 190, fontWeight: 700,
      },
    }];
  }) : rawNodes;

  const visibleNodeIds = new Set(visibleNodes.map(node => node.id));
  const rawNodeById = Object.fromEntries(rawNodes.map(node => [node.id, node]));
  const visibleEdges = [];
  const seenVisibleEdges = new Set();
  rawEdges.forEach(edge => {
    const sourceNode = rawNodeById[edge.source];
    const targetNode = rawNodeById[edge.target];
    if (!sourceNode || !targetNode) return;
    const sourcePath = sourceNode.data.metadata?.metadata?.path || sourceNode.data.metadata?.label || 'root';
    const targetPath = targetNode.data.metadata?.metadata?.path || targetNode.data.metadata?.label || 'root';
    const sourceCluster = clusterForPath(sourcePath);
    const targetCluster = clusterForPath(targetPath);
    const source = grouped && !expandedClusters.has(sourceCluster) ? `cluster::${sourceCluster}` : edge.source;
    const target = grouped && !expandedClusters.has(targetCluster) ? `cluster::${targetCluster}` : edge.target;
    if (source === target || !visibleNodeIds.has(source) || !visibleNodeIds.has(target)) return;
    const edgeKey = `${source}:${target}:${edge.label || edge.relationship}`;
    if (seenVisibleEdges.has(edgeKey)) return;
    seenVisibleEdges.add(edgeKey);
    visibleEdges.push({ ...edge, id: grouped ? `visible::${edgeKey}` : edge.id, source, target });
  });

  const directNodeIds = selectedNode ? new Set(
    visibleEdges.flatMap(edge => edge.source === selectedNode.id ? [edge.target] : edge.target === selectedNode.id ? [edge.source] : [])
  ) : new Set();

  const displayNodes = visibleNodes.map(node => ({
    ...node,
    style: {
      ...node.style,
      opacity: selectedNode && node.id !== selectedNode.id && !directNodeIds.has(node.id) ? 0.22 : 1,
      border: node.id === selectedNode?.id ? '2px solid #0f766e' : node.style.border,
      boxShadow: node.id === selectedNode?.id ? '0 0 0 4px rgba(15, 118, 110, .15)' : 'none',
      transition: 'opacity .2s ease, box-shadow .2s ease'
    }
  }));

  const displayEdges = visibleEdges.map(edge => ({
    ...edge,
    style: { ...edge.style, opacity: selectedNode && edge.source !== selectedNode.id && edge.target !== selectedNode.id ? 0.12 : 1 }
  }));

  const nodeLabelById = Object.fromEntries(visibleNodes.map(node => [node.id, node.data.metadata?.label || node.data.label]));

  const selectedRelationships = selectedNode ? visibleEdges.filter(edge => edge.source === selectedNode.id || edge.target === selectedNode.id) : [];
  const incomingRelationships = selectedRelationships.filter(edge => edge.target === selectedNode?.id).length;
  const outgoingRelationships = selectedRelationships.filter(edge => edge.source === selectedNode?.id).length;
  const selectedPath = selectedNode?.metadata?.path || selectedNode?.label || 'this file';
  const selectedLanguage = selectedNode?.metadata?.language || 'unknown-language';
  const selectedType = selectedNode?.type || 'service';

  const onNodeClick = async (_, node) => {
    if (node.data.cluster) {
      setExpandedClusters(previous => {
        const next = new Set(previous);
        if (next.has(node.data.clusterKey)) next.delete(node.data.clusterKey);
        else next.add(node.data.clusterKey);
        return next;
      });
      setSelectedNode(null);
      onSelectionChange?.(null);
      return;
    }
    setSelectedNode(node.data.metadata);
    onSelectionChange?.({
      ...node.data.metadata,
      relationships: visibleEdges
        .filter(edge => edge.source === node.id || edge.target === node.id)
        .map(edge => ({
          direction: edge.source === node.id ? 'outgoing' : 'incoming',
          relationship: edge.label || edge.relationship,
          connected_block: nodeLabelById[edge.source === node.id ? edge.target : edge.source] || 'related block',
          evidence: edge.evidence || {},
        })),
    });
    const fileId = node.data.metadata.file_ids?.[0];
    const file = fileIndex[fileId] || Object.values(fileIndex).find(item => item.path === node.data.metadata.metadata?.path);
    if (file) {
      const cRes = await fetch(apiUrl(`/v1/repositories/${repoId}/files/${file.id}/content`), {
        headers: { Authorization: `Bearer ${token}` }
      });
      const cData = await cRes.json();
      setSelectedFile(cData);
      setFileContent(cData.content);
    }
  };

  return (
    <div className="graph-shell">
      <div className="graph-pane">
        {grouped && <div className="graph-note">Large repository detected - grouped view is active. Click a folder to expand its files.</div>}
        <button className="button button-quiet graph-load" onClick={fetchGraph} title="Reload architecture graph">
          <RefreshCw size={15} /> Load Graph
        </button>
        <ReactFlow nodes={displayNodes} edges={displayEdges} onNodeClick={onNodeClick} onPaneClick={() => { setSelectedNode(null); onSelectionChange?.(null); }} fitView>
          <Background />
          <Controls />
          <MiniMap />
        </ReactFlow>
      </div>

      {selectedNode && <aside className="node-drawer" aria-label="Architecture block details">
        <div className="drawer-header">
          <div><span className="eyebrow">Architecture block</span><h3>{selectedNode.label}</h3></div>
          <button className="button button-quiet" onClick={() => setSelectedNode(null)} title="Close details"><X size={15} /></button>
        </div>
        <div className="drawer-meta"><span>{selectedNode.metadata?.path || 'Path unavailable'}</span><b>{selectedNode.metadata?.language || 'unknown'}</b></div>
        <section className="drawer-section"><h4>What this block is</h4><p>{selectedNode.metadata?.symbol || `A ${selectedLanguage} file represented as a ${selectedType} architecture block.`}</p></section>
        <section className="drawer-section"><h4>Summary</h4><p>{selectedNode.metadata?.summary || `${selectedPath} is classified as a ${selectedType} block. It has ${incomingRelationships} incoming and ${outgoingRelationships} outgoing extracted relationship${incomingRelationships + outgoingRelationships === 1 ? '' : 's'}.`}</p></section>
        <section className="drawer-section"><h4>Relationships</h4>
          {selectedRelationships.length === 0 && <p className="drawer-muted">No direct relationships were extracted from this file.</p>}
          {visibleEdges.filter(edge => edge.source === selectedNode.id).map(edge => <div className="relationship-row" key={`out-${edge.id}`}><ArrowUpRight size={14} /><span>outgoing {edge.label || edge.relationship} to {nodeLabelById[edge.target] || 'related block'}</span></div>)}
          {visibleEdges.filter(edge => edge.target === selectedNode.id).map(edge => <div className="relationship-row" key={`in-${edge.id}`}><ArrowDownRight size={14} /><span>incoming {edge.label || edge.relationship} from {nodeLabelById[edge.source] || 'related block'}</span></div>)}
        </section>
      </aside>}
      
      {selectedFile && (
        <div className="editor-pane">
          <div className="editor-header">
            <strong>{selectedFile.path}</strong>
            <button className="button button-quiet" onClick={() => setSelectedFile(null)} title="Close file viewer"><X size={15} /></button>
          </div>
          <Editor
            height="100%"
            language={selectedFile.language || 'text'}
            value={fileContent}
            options={{ readOnly: true, minimap: { enabled: false } }}
          />
        </div>
      )}
    </div>
  );
}
