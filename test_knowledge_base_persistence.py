import os
import sys

# 模拟应用重启。
# 这个脚本验证知识库向量文件和 Faiss 索引是否能在进程重启后被重新发现。
def test_knowledge_base_persistence():
    """创建测试知识库 -> 清空内存实例 -> 扫描磁盘文件 -> 验证仍可恢复。"""
    print("测试知识库持久化...")
    
    # 导入QAModule
    from app.services.knowledge_service import QAModule
    
    # 1. 清除所有实例（模拟应用重启）。
    QAModule._instances = {}
    print("模拟应用重启，清除所有实例")
    
    # 2. 获取当前知识库列表（应该只有默认知识库）
    kb_list = QAModule.get_all_instances()
    print(f"重启后初始知识库列表: {kb_list}")
    
    # 3. 模拟创建一个新知识库
    new_kb_name = "test_kb"
    print(f"创建新知识库: {new_kb_name}")
    
    # 初始化新知识库
    qa_module = QAModule(new_kb_name)
    
    # 添加一个简单的文档
    test_file_path = "test_kb_file.txt"
    with open(test_file_path, 'w', encoding='utf-8') as f:
        f.write("这是测试知识库的内容")
    
    # 添加文档到知识库，会生成 vector_store.pkl 和 faiss.index 两类持久化文件。
    qa_module.add_document(test_file_path, metadata={"test": "metadata"})
    
    # 验证文件是否创建
    data_file = f"./knowledge_base_{new_kb_name}_vector_store.pkl"
    index_file = f"./knowledge_base_{new_kb_name}_faiss.index"
    
    if os.path.exists(data_file) and os.path.exists(index_file):
        print(f"✅ 知识库文件创建成功: {data_file} 和 {index_file}")
    else:
        print("❌ 知识库文件创建失败")
    
    # 4. 再次清除所有实例（模拟应用重启）
    QAModule._instances = {}
    print("再次模拟应用重启，清除所有实例")
    
    # 5. 模拟调用 /api/knowledge-base/list 端点
    print("模拟调用知识库列表端点...")
    
    # 扫描文件系统中的知识库文件，逻辑与 /api/knowledge-base/list 保持一致。
    knowledge_bases = []
    for file in os.listdir('.'):
        if file.endswith('_vector_store.pkl'):
            # 提取知识库名称
            kb_name = file.replace('knowledge_base_', '').replace('_vector_store.pkl', '')
            if kb_name not in knowledge_bases:
                knowledge_bases.append(kb_name)
                # 初始化知识库
                QAModule(kb_name)
    
    # 确保默认知识库存在
    if 'default' not in knowledge_bases:
        knowledge_bases.append('default')
        # 初始化默认知识库
        QAModule('default')
    
    print(f"重启后扫描到的知识库列表: {knowledge_bases}")
    
    # 6. 验证新知识库是否存在
    if new_kb_name in knowledge_bases:
        print(f"✅ 测试通过: 知识库 {new_kb_name} 在重启后仍然存在")
    else:
        print(f"❌ 测试失败: 知识库 {new_kb_name} 在重启后消失了")
    
    # 清理测试文件，避免本地测试数据污染真实知识库列表。
    if os.path.exists(test_file_path):
        os.remove(test_file_path)
    if os.path.exists(data_file):
        os.remove(data_file)
    if os.path.exists(index_file):
        os.remove(index_file)
    
    print("测试完成")

if __name__ == "__main__":
    test_knowledge_base_persistence()
