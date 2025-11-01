//! Chat interface and UI deployment

use anyhow::Result;
use styx_sky::{Task, Resources, launch};

/// Chat UI type
#[derive(Debug, Clone, Copy)]
pub enum ChatUI {
    Gradio,
    StreamLit,
    ChainLit,
    OpenWebUI,
}

/// Chat interface builder
pub struct ChatInterface {
    model: String,
    ui_type: ChatUI,
    port: u16,
}

impl ChatInterface {
    pub fn new(model: impl Into<String>) -> Self {
        Self {
            model: model.into(),
            ui_type: ChatUI::Gradio,
            port: 7860,
        }
    }
    
    pub fn with_ui(mut self, ui_type: ChatUI) -> Self {
        self.ui_type = ui_type;
        self
    }
    
    pub fn with_port(mut self, port: u16) -> Self {
        self.port = port;
        self
    }
    
    /// Deploy chat interface
    pub async fn deploy(&self) -> Result<String> {
        let task = Task::new()
            .with_name(&format!("chat-{}", match self.ui_type {
                ChatUI::Gradio => "gradio",
                ChatUI::StreamLit => "streamlit",
                ChatUI::ChainLit => "chainlit",
                ChatUI::OpenWebUI => "openwebui",
            }))
            .with_setup(&self.generate_setup())
            .with_run(&self.generate_run())
            .with_resources(
                Resources::new()
                    .with_accelerator("A100", 2)
                    .with_port(self.port)
            );
        
        launch(task, None, false).await
    }
    
    fn generate_setup(&self) -> String {
        match self.ui_type {
            ChatUI::Gradio => self.setup_gradio(),
            ChatUI::StreamLit => self.setup_streamlit(),
            ChatUI::ChainLit => self.setup_chainlit(),
            ChatUI::OpenWebUI => self.setup_openwebui(),
        }
    }
    
    fn setup_gradio(&self) -> String {
        r#"
conda activate chat || conda create -n chat python=3.10 -y
conda activate chat
pip install gradio transformers torch accelerate
"#.to_string()
    }
    
    fn setup_streamlit(&self) -> String {
        r#"
conda activate chat || conda create -n chat python=3.10 -y
conda activate chat
pip install streamlit transformers torch
"#.to_string()
    }
    
    fn setup_chainlit(&self) -> String {
        r#"
conda activate chat || conda create -n chat python=3.10 -y
conda activate chat
pip install chainlit langchain openai
"#.to_string()
    }
    
    fn setup_openwebui(&self) -> String {
        r#"
# OpenWebUI uses Docker
docker pull ghcr.io/open-webui/open-webui:main
"#.to_string()
    }
    
    fn generate_run(&self) -> String {
        match self.ui_type {
            ChatUI::Gradio => self.run_gradio(),
            ChatUI::StreamLit => self.run_streamlit(),
            ChatUI::ChainLit => self.run_chainlit(),
            ChatUI::OpenWebUI => self.run_openwebui(),
        }
    }
    
    fn run_gradio(&self) -> String {
        format!(r#"
python -c "
import gradio as gr
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

# Load model
tokenizer = AutoTokenizer.from_pretrained('{}')
model = AutoModelForCausalLM.from_pretrained(
    '{}',
    torch_dtype=torch.float16,
    device_map='auto'
)

def chat(message, history):
    # Format prompt
    prompt = message
    
    # Generate
    inputs = tokenizer(prompt, return_tensors='pt').to('cuda')
    outputs = model.generate(**inputs, max_new_tokens=512)
    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    
    return response

# Create interface
demo = gr.ChatInterface(
    fn=chat,
    title='LLM Chat',
    description='Chat with {}',
)

demo.launch(server_name='0.0.0.0', server_port={})
"
"#, self.model, self.model, self.model, self.port)
    }
    
    fn run_streamlit(&self) -> String {
        format!(r#"
# Create Streamlit app
cat > app.py << 'EOF'
import streamlit as st
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

st.title('LLM Chat')

@st.cache_resource
def load_model():
    tokenizer = AutoTokenizer.from_pretrained('{}')
    model = AutoModelForCausalLM.from_pretrained(
        '{}',
        torch_dtype=torch.float16,
        device_map='auto'
    )
    return tokenizer, model

tokenizer, model = load_model()

# Chat interface
if 'messages' not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])

if prompt := st.chat_input('Message'):
    st.session_state.messages.append({{'role': 'user', 'content': prompt}})
    
    with st.chat_message('user'):
        st.markdown(prompt)
    
    with st.chat_message('assistant'):
        inputs = tokenizer(prompt, return_tensors='pt').to('cuda')
        outputs = model.generate(**inputs, max_new_tokens=512)
        response = tokenizer.decode(outputs[0], skip_special_tokens=True)
        st.markdown(response)
        st.session_state.messages.append({{'role': 'assistant', 'content': response}})
EOF

# Run Streamlit
streamlit run app.py --server.port {}
"#, self.model, self.model, self.port)
    }
    
    fn run_chainlit(&self) -> String {
        format!(r#"
# Create Chainlit app
cat > app.py << 'EOF'
import chainlit as cl

@cl.on_chat_start
async def start():
    await cl.Message(content='Hello! How can I help you?').send()

@cl.on_message
async def main(message: cl.Message):
    # TODO: Integrate with LLM
    response = f'You said: {{message.content}}'
    await cl.Message(content=response).send()
EOF

# Run Chainlit
chainlit run app.py --port {}
"#, self.port)
    }
    
    fn run_openwebui(&self) -> String {
        format!(r#"
docker run -d \
  -p {}:8080 \
  --gpus all \
  -v open-webui:/app/backend/data \
  --name open-webui \
  ghcr.io/open-webui/open-webui:main
"#, self.port)
    }
}

/// Vicuna chat template
pub struct VicunaChat {
    model: String,
}

impl VicunaChat {
    pub fn new(model: impl Into<String>) -> Self {
        Self {
            model: model.into(),
        }
    }
    
    pub async fn deploy(&self) -> Result<String> {
        ChatInterface::new(&self.model)
            .with_ui(ChatUI::Gradio)
            .deploy()
            .await
    }
}
