from app.services.knowledge_service import QAModule

# 创建测试知识库
test_kb_name = "test_persistence"
print(f"创建测试知识库: {test_kb_name}")

# 初始化知识库
qa_module = QAModule(test_kb_name)

# 添加一个简单的文档
test_content = "这是测试知识库持久化的内容"
test_file_path = f"{test_kb_name}_test.txt"

with open(test_file_path, 'w', encoding='utf-8') as f:
    f.write(test_content)

# 添加文档到知识库
qa_module.add_document(test_file_path, metadata={"test": "persistence"})

print(f"知识库 {test_kb_name} 创建成功")
print("请重启应用后验证知识库是否存在")
