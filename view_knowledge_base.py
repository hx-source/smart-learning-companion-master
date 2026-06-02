import pickle

# 读取知识库文件
file_path = './knowledge_base_default_vector_store.pkl'

print(f"读取知识库文件: {file_path}")

try:
    with open(file_path, 'rb') as f:
        data = pickle.load(f)
    
    print(f"\n知识库内容:")
    print(f"文档数量: {len(data.get('documents', []))}")
    print(f"元数据数量: {len(data.get('metadatas', []))}")
    print(f"ID数量: {len(data.get('ids', []))}")
    
    # 打印前几个文档的内容
    documents = data.get('documents', [])
    metadatas = data.get('metadatas', [])
    ids = data.get('ids', [])
    
    print(f"\n前5个文档:")
    for i, (doc, meta, id) in enumerate(zip(documents[:5], metadatas[:5], ids[:5])):
        print(f"\n文档 {i+1} (ID: {id}):")
        print(f"内容: {doc[:200]}..." if len(doc) > 200 else f"内容: {doc}")
        print(f"元数据: {meta}")
        
    if len(documents) > 5:
        print(f"\n... 还有 {len(documents) - 5} 个文档")
        
except Exception as e:
    print(f"读取文件失败: {str(e)}")
