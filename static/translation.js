const translations = {
    // --- English Content ---
    en: {
        // Navigation & Footer
        nav_home: "Home",
        nav_models: "Models",
        nav_pricing: "Pricing",
        nav_enterprise: "Custom Solutions",
        footer_rights: "All rights reserved © 2026 Nexus API",

        // Buttons & Labels
        btn_view_details: "View Details & Get Key",
        btn_back_catalog: "Back to Catalog",
        btn_get_key: "Generate API Key",
        btn_explore: "Explore Models",
        btn_trial: "Start Free Trial",
        btn_copy_code: "Copy Code",
        btn_upload: "Upload File",
        btn_process: "Process",

        // Stats Section
        stat_uptime: "Uptime SLA",
        stat_retention: "Data Retention",
        stat_unified: "Unified API",
        stat_compatible: "OpenAI Compatible",

        // General Text
        txt_live_access: "Live Access",
        txt_instant_access: "Instant access, no credit card required",
        lbl_key_title: "Access Key",
        lbl_python_example: "Python Integration Example",
        lbl_integration_example: "Full Integration Snippet",
        msg_code_hint: "This code is pre-configured for the Nexus Gateway. Copy & Run.",
        lbl_instructions: "Instructions",
        lbl_source_code: "Source Code",

        // NEW KEYS (Terminal & Inputs)
        ph_code_snippet: "Paste the code you want to merge directly or upload the file.",
        lbl_output_title: "Model Response / Terminal",
        msg_output_waiting: "System ready. Waiting for input...",
        msg_processing: "Processing Logic...",

        // Home Page
        home_title_prefix: "NEXUS",
        home_title_suffix: "INFRA",
        home_subtitle: "Unified AI Infrastructure. Instant access to the latest DeepSeek and Mistral models via a single, secure, and fast API Gateway.",

        // Why Nexus Section
        why_title: "Why Nexus?",
        why_heading: "Enterprise Solutions for<br>AI Model Management.",
        why_desc: "We provide a radical solution to provider fragmentation. Instead of managing multiple keys and invoices, Nexus offers a Unified Gateway ensuring service stability, Standardized Output, and data privacy.",
        sec_security_title: "Strict Security Protocol",
        sec_security_desc: "Acting as a firewall; your data is never stored or used for model training.",
        sec_latency_title: "Low Latency Infrastructure",
        sec_latency_desc: "Distributed servers ensuring the lowest possible latency for your applications.",

        // Models Page Titles
        catalog_title_prefix: "AI Models",
        catalog_title_suffix: "Catalog",
        catalog_subtitle: "Choose the right engine for your data. All models are available via a unified subscription.",

        // Tabs
        tab_live_chat: "Live Chat",
        tab_live_chat_desc: "Direct chat to test model response.",
        tab_integration: "Code Integration",
        tab_integration_desc: "Upload files (py, java, cpp) for auto-optimization.",

        // --- EXACT MODEL DESCRIPTIONS (ENGLISH) ---
        models: {
            "meta/llama-3.2-3b-instruct": {

    name: "Llama 3.2 3B",

    short_desc: "Optimized lightweight model for instant interactions and creative content.",

    full_html: `

        <h3 class="text-2xl font-bold mb-4">Meta Llama 3.2 3B: Efficiency Meets Intelligence</h3>

        <p class="mb-4 text-gray-600 dark:text-gray-300">Llama 3.2 3B Instruct represents a quantum leap in the lightweight model category. It combines compact size with high cognitive capabilities, making it the premier choice for developers seeking a chatbot engine with instant response times and high linguistic precision. It is ideal for applications requiring continuous, delay-free user interaction.</p>

        <p class="mb-6 text-gray-600 dark:text-gray-300">This model stands out as a strategic solution for managing complex logical tasks, summarization, and creative content generation while maintaining amazing operational efficiency. It outperforms much larger models in context understanding and instruction following.</p>

        <h4 class="text-xl font-bold mb-4 text-primary">Technical Specifications:</h4>

        <div class="overflow-x-auto">

            <table class="w-full text-sm text-left border border-gray-200 dark:border-gray-700">

                <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">

                    <tr><th class="px-4 py-3">Feature</th><th class="px-4 py-3">Specification</th></tr>

                </thead>

                <tbody class="divide-y divide-gray-200 dark:divide-gray-700">

                    <tr><td class="px-4 py-2 font-semibold">Model Name</td><td class="px-4 py-2">Meta Llama 3.2 3B Instruct</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Release Date</td><td class="px-4 py-2">September 25, 2024</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Category</td><td class="px-4 py-2">Lightweight Advanced LLM</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Context Window</td><td class="px-4 py-2">128,000 Tokens</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Performance</td><td class="px-4 py-2">Superior Instruction Following</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Best Use</td><td class="px-4 py-2">Personal Assistants, Summarization, Automated Support</td></tr>

                </tbody>

            </table>

        </div>

    `

},

"google/gemma-3n-e4b-it": {

    name: "Gemma 3 4B-IT",

    short_desc: "Google's latest efficient model. High speed with multimodal capabilities.",

    full_html: `

        <h3 class="text-2xl font-bold mb-4">Gemma 3 4B-IT: Next-Gen Speed & Efficiency</h3>

        <p class="mb-4 text-gray-600 dark:text-gray-300">Gemma 3 4B-IT is Google's latest innovation in high-efficiency AI. Designed specifically for developers prioritizing "speed" and "instant response," its advanced architecture delivers stunning performance in multilingual tasks and multimodal processing. It is the perfect engine for building lightning-fast chatbots and digital assistants at very low operational costs.</p>

        <p class="mb-6 text-gray-600 dark:text-gray-300">Distinctive for its ability to handle massive contexts up to 128,000 tokens with the lowest latency in its class. Whether for customer service, visual data extraction, or complex dialogue management, Gemma 3 provides the perfect balance of practical intelligence and resource economy.</p>

        <h4 class="text-xl font-bold mb-4 text-primary">Technical Specifications:</h4>

        <div class="overflow-x-auto">

            <table class="w-full text-sm text-left border border-gray-200 dark:border-gray-700">

                <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">

                    <tr><th class="px-4 py-3">Feature</th><th class="px-4 py-3">Specification</th></tr>

                </thead>

                <tbody class="divide-y divide-gray-200 dark:divide-gray-700">

                    <tr><td class="px-4 py-2 font-semibold">Model Name</td><td class="px-4 py-2">Google Gemma 3 4B-IT (Enhanced)</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Release Date</td><td class="px-4 py-2">March 11, 2025</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Category</td><td class="px-4 py-2">Multimodal Intelligent Model</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Context Window</td><td class="px-4 py-2">128,000 Tokens</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Modalities</td><td class="px-4 py-2">Text, Images, Visual Documents</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">Latency</td><td class="px-4 py-2">Ultra-low (Real-time optimized)</td></tr>

                </tbody>

            </table>

        </div>

    `

},

            
            "deepseek-ai/deepseek-v3.2": {
                name: "Deepseek 3.2",
                short_desc: "Currently the strongest open-source model. Excels in coding and mathematical logic.",
                full_html: `
                    <h3 class="text-2xl font-bold mb-4">Elevate Your Intelligent Applications with DeepSeek-V3.2</h3>
                    <p class="mb-4 text-gray-600 dark:text-gray-300">DeepSeek-V3.2 represents the pinnacle of efficient large language models, designed to provide enterprise-grade performance through a highly optimized Mixture-of-Experts (MoE) architecture. This model is engineered for businesses seeking a balance between sophisticated reasoning and cost-effective scalability. By integrating DeepSeek-V3.2 via our API, you gain access to a powerhouse capable of handling complex multilingual tasks, advanced code generation, and nuanced content synthesis with exceptional precision.</p>
                    <p class="mb-6 text-gray-600 dark:text-gray-300">With its massive parameter scale, the model demonstrates fluid adaptability across diverse domains, ensuring that your applications remain at the forefront of the AI revolution. It offers a seamless experience that matches the output quality of industry leaders like GPT-4o, making it an ideal choice for high-throughput production environments where reliability and intelligence are paramount.</p>
                    <h4 class="text-xl font-bold mb-4 text-primary">Key Technical Specifications:</h4>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm text-left border border-gray-200 dark:border-gray-700">
                            <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">
                                <tr><th class="px-4 py-3">Feature</th><th class="px-4 py-3">Specification</th></tr>
                            </thead>
                            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                                <tr><td class="px-4 py-2 font-semibold">Model Name</td><td class="px-4 py-2">DeepSeek-V3.2 (Base)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Release Date</td><td class="px-4 py-2">December 1, 2025</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Architecture</td><td class="px-4 py-2">Multi-head Latent Attention (MLA) & DeepSeekMoE</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Total Parameters</td><td class="px-4 py-2">671 Billion</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Activated Parameters</td><td class="px-4 py-2">37 Billion (per token)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Context Window</td><td class="px-4 py-2">128,000 Tokens</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Max Output Length</td><td class="px-4 py-2">131,072 Tokens</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Supported Modalities</td><td class="px-4 py-2">Multilingual Text, Code, Reasoning</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Inference Optimization</td><td class="px-4 py-2">Multi-Token Prediction (MTP) Technology</td></tr>
                            </tbody>
                        </table>
                    </div>
                `
            },
            "mistralai/mistral-large-3-675b-instruct-2512": {
                name: "Mistral 3 large",
                short_desc: "European precision for business, ideal for following complex instructions.",
                full_html: `
                    <h3 class="text-2xl font-bold mb-4">Unleash Next-Generation Intelligence with Mistral Large 3</h3>
                    <p class="mb-4 text-gray-600 dark:text-gray-300">Mistral Large 3 stands as the most advanced frontier model from Mistral AI, meticulously engineered to redefine the standards of enterprise-level artificial intelligence. Optimized for high-stakes production environments, this model excels in complex reasoning, advanced multilingual understanding, and sophisticated code generation. By integrating Mistral Large 3 via our API, your applications leverage a state-of-the-art architecture designed for maximum efficiency and precision in handling long-context tasks.</p>
                    <p class="mb-6 text-gray-600 dark:text-gray-300">In terms of performance, Mistral Large 3 doesn't just keep pace with the industry; it actively rivals and, in several key reasoning and coding benchmarks, surpasses the leading proprietary models available today. It is the premier choice for businesses that demand top-tier cognitive capabilities without compromising on speed or reliability.</p>
                    <h4 class="text-xl font-bold mb-4 text-primary">Technical Specifications:</h4>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm text-left border border-gray-200 dark:border-gray-700">
                            <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">
                                <tr><th class="px-4 py-3">Feature</th><th class="px-4 py-3">Specification</th></tr>
                            </thead>
                            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                                <tr><td class="px-4 py-2 font-semibold">Model Name</td><td class="px-4 py-2">Mistral Large 3 (675B)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Release Date</td><td class="px-4 py-2">December 2, 2025</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Architecture</td><td class="px-4 py-2">Dense Transformer</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Total Parameters</td><td class="px-4 py-2">675 Billion</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Context Window</td><td class="px-4 py-2">128,000 Tokens</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Multilingual Support</td><td class="px-4 py-2">Native support for 80+ languages</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Coding Proficiency</td><td class="px-4 py-2">Exceptional (Top-tier in Python, C++, Java, etc.)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Reasoning Capability</td><td class="px-4 py-2">Advanced Logical & Mathematical Reasoning</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Optimization</td><td class="px-4 py-2">Native Function Calling & JSON Output Mode</td></tr>
                            </tbody>
                        </table>
                    </div>
                `
            },
            "moonshotai/kimi-k2-thinking": {
                name: "Kimi 2 thinking",
                short_desc: "Strategic reasoning engine based on Chain of Thought.",
                full_html: `
                    <h3 class="text-2xl font-bold mb-4">Master Complex Problem Solving with Kimi-K2</h3>
                    <p class="mb-4 text-gray-600 dark:text-gray-300">Kimi-K2 is a frontier reasoning model developed by Moonshot AI, specifically engineered to tackle the most demanding intellectual challenges. Unlike standard models, Kimi-K2 utilizes an advanced internal "thinking" process, allowing it to verify facts and explore multiple logic paths before providing a final answer. This makes it an indispensable tool for scientific research, advanced mathematical proofs, and intricate system architectural design.</p>
                    <p class="mb-6 text-gray-600 dark:text-gray-300">By integrating Kimi-K2 via our API, you gain a model that doesn't just generate text, but actively "reasons" through tasks. It actively competes with and often surpasses high-end closed models like o1-preview in complex mathematical benchmarks and coding logic. If your project requires a higher degree of accuracy and a rigorous chain-of-thought, Kimi-K2 is the optimal solution for your high-level intelligent agents.</p>
                    <h4 class="text-xl font-bold mb-4 text-primary">Technical Specifications:</h4>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm text-left border border-gray-200 dark:border-gray-700">
                            <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">
                                <tr><th class="px-4 py-3">Feature</th><th class="px-4 py-3">Specification</th></tr>
                            </thead>
                            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                                <tr><td class="px-4 py-2 font-semibold">Model Name</td><td class="px-4 py-2">Kimi-K2 (Thinking Model)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Release Date</td><td class="px-4 py-2">November 6, 2025</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Primary Strength</td><td class="px-4 py-2">Reinforcement Learning-based Reasoning</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Context Window</td><td class="px-4 py-2">128,000 Tokens</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Performance Benchmark</td><td class="px-4 py-2">Competes with o1 in Math & STEM</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Programming</td><td class="px-4 py-2">Advanced System-level Coding & Debugging</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Inference Mode</td><td class="px-4 py-2">Real-time Chain-of-Thought (CoT)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">Multilingual Support</td><td class="px-4 py-2">Comprehensive (Optimized for Arabic/English/Chinese)</td></tr>
                            </tbody>
                        </table>
                    </div>
                `
            }
        }
    },

    // --- Arabic Content ---
    ar: {
        // Navigation & Footer
        nav_home: "الرئيسية",
        nav_models: "النماذج",
        nav_pricing: "الأسعار",
        nav_enterprise: "حلول مخصصة", // <-- تم إصلاح الفاصلة هنا
        footer_rights: "جميع الحقوق محفوظة © 2026 Nexus API",

        // Buttons & Labels
        btn_view_details: "عرض التفاصيل والحصول على المفتاح",
        btn_back_catalog: "العودة للكتالوج",
        btn_get_key: "استخراج مفتاح API",
        btn_explore: "استكشف النماذج",
        btn_trial: "ابدأ التجربة المجانية",
        btn_copy_code: "نسخ الكود",
        btn_upload: "رفع ملف",
        btn_process: "تنفيذ المعالجة",

        // General Text
        txt_live_access: "وصول فوري (Live Access)",
        txt_instant_access: "تفعيل فوري بدون بطاقة ائتمان",
        lbl_key_title: "مفتاح الوصول (KEY)",
        lbl_python_example: "مثال دمج بايثون",
        lbl_integration_example: "كود التكامل الكامل (Python)",
        msg_code_hint: "هذا الكود مُجهز مسبقاً للعمل مع بوابة Nexus. قم بنسخه وتشغيله مباشرة.",
        lbl_instructions: "التعليمات",
        lbl_source_code: "الكود المصدري",

        // NEW KEYS (Terminal & Inputs)
        ph_code_snippet: "ضع الكود المراد دمجه بشكل مباشر أو قم بتحميل الملف.",
        msg_processing: "جاري تحليل المنطق...",
        lbl_output_title: "استجابة النموذج / Terminal",
        msg_output_waiting: "النظام جاهز. في انتظار المدخلات...",

        // Stats Section
        stat_uptime: "ضمان وقت التشغيل",
        stat_retention: "احتفاظ بالبيانات",
        stat_unified: "واجهة موحدة",
        stat_compatible: "متوافق مع OpenAI",

        // Home Page
        home_title_prefix: "بنية",
        home_title_suffix: "نكساس",
        home_subtitle: "بنية تحتية موحدة للذكاء الاصطناعي. وصول فوري لأحدث نماذج DeepSeek و Mistral عبر واجهة برمجية واحدة (API Gateway) آمنة وسريعة.",

        // Why Nexus Section
        why_title: "لماذا Nexus؟",
        why_heading: "حلول مؤسسية لإدارة<br>نماذج الذكاء الاصطناعي.",
        why_desc: "نقدم حلاً جذرياً لمشاكل التشتت بين مزودي الخدمة. بدلاً من إدارة مفاتيح متعددة وفواتير متفرقة، توفر Nexus بوابة موحدة (Unified Gateway) تضمن استقرار الخدمة، وتوحيد تنسيق البيانات (Standardized Output)، وحماية الخصوصية.",
        sec_security_title: "بروتوكول أمان صارم",
        sec_security_desc: "نعمل كجدار حماية (Firewall)؛ بياناتك لا تُخزن ولا تُستخدم لتدريب النماذج.",
        sec_latency_title: "بنية تحتية منخفضة الكمون",
        sec_latency_desc: "خوادم موزعة لضمان أقل زمن استجابة (Latency) ممكن لتطبيقاتك.",

        // Models Page Titles
        catalog_title_prefix: "كتالوج",
        catalog_title_suffix: "النماذج الذكية",
        catalog_subtitle: "اختر المحرك المناسب لطبيعة بياناتك. جميع النماذج متاحة عبر اشتراك موحد.",

        // Tabs
        tab_live_chat: "تجربة حية (Live Chat)",
        tab_live_chat_desc: "محادثة مباشرة للتأكد من استجابة النموذج.",
        tab_integration: "دمج الملفات (Code Integration)",
        tab_integration_desc: "رفع ملفات (py, java, cpp) وتعديلها تلقائياً.",

        // --- EXACT MODEL DESCRIPTIONS (ARABIC) ---
        models: {
            "meta/llama-3.2-3b-instruct": {

    name: "Llama 3.2 3B",

    short_desc: "قفزة نوعية في النماذج المتوسطة. سرعة لحظية ودقة عالية.",

    full_html: `

        <h3 class="text-2xl font-bold mb-4">Meta Llama 3.2 3B: عندما تجتمع السرعة مع الذكاء</h3>

        <p class="mb-4 text-gray-600 dark:text-gray-300">يُعد نموذج Llama 3.2 3B Instruct قفزة نوعية في فئة النماذج المتوسطة، حيث يجمع بين صغر الحجم والقدرات الذهنية العالية التي تميز عائلة Meta. تم تحسين هذا النموذج بدقة ليكون الخيار الأول للمطورين الذين يبحثون عن محرك محادثة (Chatbot) يمتاز بالاستجابة اللحظية والدقة اللغوية العالية، مما يجعله مثالياً للتطبيقات التي تتطلب تفاعلاً مستمراً مع المستخدمين دون تأخير.</p>

        <p class="mb-6 text-gray-600 dark:text-gray-300">يبرز Llama 3.2 3B كحل استراتيجي لإدارة المهام المنطقية المعقدة، التلخيص، وصياغة المحتويات الإبداعية، مع الحفاظ على كفاءة تشغيلية مذهلة. بفضل تحسيناته الأخيرة، يقدم النموذج أداءً يتفوق على نماذج أكبر منه بكثير في فهم السياق واتباع التعليمات بدقة، مما يجعله المحرك الأكثر موثوقية وتوازناً في منصتنا للمشاريع التي تستهدف الجمع بين تجربة المستخدم السلسة والتكاليف الذكية.</p>

        <h4 class="text-xl font-bold mb-4 text-primary">جدول المواصفات الفنية:</h4>

        <div class="overflow-x-auto">

            <table class="w-full text-sm text-right border border-gray-200 dark:border-gray-700">

                <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">

                    <tr><th class="px-4 py-3">الميزة</th><th class="px-4 py-3">المواصفات</th></tr>

                </thead>

                <tbody class="divide-y divide-gray-200 dark:divide-gray-700">

                    <tr><td class="px-4 py-2 font-semibold">اسم النموذج</td><td class="px-4 py-2">Meta Llama 3.2 3B Instruct</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">تاريخ الإصدار</td><td class="px-4 py-2">25 سبتمبر 2024</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">التصنيف</td><td class="px-4 py-2">نموذج لغوي متطور فئة Lightweight</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">نافذة السياق</td><td class="px-4 py-2">128,000 توكن</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">نمط المعالجة</td><td class="px-4 py-2">نصوص (Text-to-Text)</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">قوة الأداء</td><td class="px-4 py-2">متفوق في اتباع التعليمات (Instruction Following)</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">الاستخدام الأمثل</td><td class="px-4 py-2">المساعدات الشخصية، تلخيص البيانات، ودعم العملاء المؤتمت</td></tr>

                </tbody>

            </table>

        </div>

    `

},

"google/gemma-3n-e4b-it": {

    name: "Gemma 3 4B-IT",

    short_desc: "الجيل الأحدث من Google. سرعة فائقة مع قدرات متعددة الوسائط.",

    full_html: `

        <h3 class="text-2xl font-bold mb-4">Gemma 3 4B-IT: سرعة الاستجابة القصوى</h3>

        <p class="mb-4 text-gray-600 dark:text-gray-300">يُمثل Gemma 3 4B-IT الجيل الأحدث من ابتكارات Google في عالم النماذج الذكية عالية الكفاءة. تم تصميم هذا النموذج خصيصاً للمطورين والشركات التي تضع "السرعة" و"الاستجابة اللحظية" كأولوية قصوى في تطبيقاتها. بفضل بنيته المتطورة، يقدم النموذج أداءً مذهلاً في المهام اللغوية المتعددة ومعالجة البيانات البصرية (Multimodal)، مما يجعله المحرك المثالي لبناء روبوتات المحادثة الذكية والمساعدين الرقميين الذين يتفاعلون مع المستخدمين بسرعة البرق وبتكلفة تشغيلية منخفضة جداً.</p>

        <p class="mb-6 text-gray-600 dark:text-gray-300">ما يميز Gemma 3 4B-IT في بيئة الإنتاج هو قدرته الفائقة على معالجة سياقات ضخمة تصل إلى 128,000 توكن، مع الحفاظ على زمن استجابة (Latency) هو الأقل في فئته. سواء كنت ترغب في بناء نظام لخدمة العملاء، أو أداة لاستخراج البيانات من الصور والمستندات، أو محركاً لإدارة الحوارات المعقدة، فإن هذا النموذج يوفر لك التوازن المثالي بين الذكاء العملي والاقتصاد في استهلاك الموارد.</p>

        <h4 class="text-xl font-bold mb-4 text-primary">جدول المواصفات الفنية:</h4>

        <div class="overflow-x-auto">

            <table class="w-full text-sm text-right border border-gray-200 dark:border-gray-700">

                <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">

                    <tr><th class="px-4 py-3">الميزة</th><th class="px-4 py-3">المواصفات</th></tr>

                </thead>

                <tbody class="divide-y divide-gray-200 dark:divide-gray-700">

                    <tr><td class="px-4 py-2 font-semibold">اسم النموذج</td><td class="px-4 py-2">Google Gemma 3 4B-IT (النسخة المطورة)</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">تاريخ الإصدار</td><td class="px-4 py-2">11 مارس 2025</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">التصنيف</td><td class="px-4 py-2">نموذج ذكي متعدد الوسائط (Multimodal)</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">نافذة السياق</td><td class="px-4 py-2">128,000 توكن</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">أنماط المعالجة</td><td class="px-4 py-2">النصوص، الصور، وتحليل المستندات البصرية</td></tr>

                    <tr><td class="px-4 py-2 font-semibold">سرعة الاستجابة</td><td class="px-4 py-2">فائقة (Optimized for Real-time apps)</td></tr>

                </tbody>

            </table>

        </div>

    `

}
            "deepseek-ai/deepseek-v3.2": {
                name: "Deepseek 3.2",
                short_desc: "أقوى نموذج مفتوح المصدر حالياً. يتفوق في البرمجة والمنطق الرياضي.",
                full_html: `
                    <h3 class="text-2xl font-bold mb-4">ارتقِ بتطبيقاتك الذكية مع نموذج DeepSeek-V3.2</h3>
                    <p class="mb-4 text-gray-600 dark:text-gray-300">يمثل DeepSeek-V3.2 ذروة الكفاءة في نماذج اللغة الضخمة، حيث تم تصميمه لتقديم أداء بمستوى المؤسسات من خلال بنية "خليط الخبراء" (MoE) المحسنة بدقة. تم هندسة هذا النموذج خصيصاً للشركات التي تبحث عن توازن مثالي بين القدرات التحليلية المعقدة والفعالية من حيث التكلفة. من خلال دمج DeepSeek-V3.2 عبر الـ API الخاص بنا، فإنك تمنح مشاريعك قدرة فائقة على معالجة المهام متعددة اللغات، توليد الأكواد البرمجية المتقدمة، وصياغة المحتوى بدقة استثنائية.</p>
                    <p class="mb-6 text-gray-600 dark:text-gray-300">بفضل حجم معاملاته الضخم، يظهر النموذج مرونة عالية في التكيف مع مختلف المجالات، مما يضمن بقاء تطبيقاتك في طليعة ثورة الذكاء الاصطناعي. يقدم النموذج تجربة سلسة تضاهي جودة مخرجات النماذج الرائدة مثل GPT-4o، مما يجعله الخيار الأمثل لبيئات الإنتاج ذات الكثافة العالية التي تتطلب الموثوقية والذكاء الفائق كمعيار أساسي.</p>
                    <h4 class="text-xl font-bold mb-4 text-primary">جدول المواصفات الفنية:</h4>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm text-right border border-gray-200 dark:border-gray-700">
                            <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">
                                <tr><th class="px-4 py-3">الميزة</th><th class="px-4 py-3">المواصفات</th></tr>
                            </thead>
                            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                                <tr><td class="px-4 py-2 font-semibold">اسم النموذج</td><td class="px-4 py-2">DeepSeek-V3.2 (النسخة الأساسية)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">تاريخ الإصدار</td><td class="px-4 py-2">1 ديسمبر 2025</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">بنية النموذج</td><td class="px-4 py-2">Multi-head Latent Attention (MLA) & DeepSeekMoE</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">إجمالي المعاملات</td><td class="px-4 py-2">671 مليار معامل</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">المعاملات النشطة</td><td class="px-4 py-2">37 مليار (لكل توكن)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">نافذة السياق</td><td class="px-4 py-2">128,000 توكن</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">أقصى طول للمخرجات</td><td class="px-4 py-2">131,072 توكن</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">القدرات المدعومة</td><td class="px-4 py-2">النصوص متعددة اللغات، البرمجة، المنطق التحليلي</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">تحسين الاستجابة</td><td class="px-4 py-2">تقنية التنبؤ المتعدد للتوكنات (MTP)</td></tr>
                            </tbody>
                        </table>
                    </div>
                `
            },
            "mistralai/mistral-large-3-675b-instruct-2512": {
                name: "Mistral 3 large",
                short_desc: "دقة أوروبية للأعمال، مثالي لاتباع التعليمات المعقدة.",
                full_html: `
                    <h3 class="text-2xl font-bold mb-4">أطلق العنان لذكاء الجيل القادم مع Mistral Large 3</h3>
                    <p class="mb-4 text-gray-600 dark:text-gray-300">يُعد Mistral Large 3 النموذج الأكثر تقدماً من شركة Mistral AI، حيث تم هندسته بدقة ليعيد تعريف معايير الذكاء الاصطناعي الموجه للمؤسسات. تم تحسين هذا النموذج لبيئات الإنتاج الحساسة، حيث يتفوق في المنطق المعقد، الفهم العميق للغات المتعددة، وتوليد الأكواد البرمجية المتقدمة. من خلال دمج Mistral Large 3 عبر الـ API الخاص بنا، ستحصل تطبيقاتك على بنية تحتية متطورة مصممة لتحقيق أقصى قدر من الكفاءة والدقة في التعامل مع سياقات البيانات الضخمة.</p>
                    <p class="mb-6 text-gray-600 dark:text-gray-300">من حيث الأداء، لا يكتفي Mistral Large 3 بمواكبة المعايير الصناعية فحسب، بل إنه يظاهي وينافس بقوة النماذج الرائدة عالمياً، بل ويتفوق عليها في العديد من اختبارات المنطق والبرمجة الأساسية. إنه الخيار الأول للشركات التي تتطلب قدرات إدراكية من الفئة العليا مع ضمان السرعة والموثوقية.</p>
                    <h4 class="text-xl font-bold mb-4 text-primary">جدول المواصفات الفنية:</h4>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm text-right border border-gray-200 dark:border-gray-700">
                            <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">
                                <tr><th class="px-4 py-3">الميزة</th><th class="px-4 py-3">المواصفات</th></tr>
                            </thead>
                            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                                <tr><td class="px-4 py-2 font-semibold">اسم النموذج</td><td class="px-4 py-2">Mistral Large 3 (675B)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">تاريخ الإصدار</td><td class="px-4 py-2">2 ديسمبر 2025</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">بنية النموذج</td><td class="px-4 py-2">Dense Transformer</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">إجمالي المعاملات</td><td class="px-4 py-2">675 مليار معامل</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">نافذة السياق</td><td class="px-4 py-2">128,000 توكن</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">دعم اللغات</td><td class="px-4 py-2">دعم أصلي لأكثر من 80 لغة</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">كفاءة البرمجة</td><td class="px-4 py-2">استثنائية (مستوى رائد في لغات Python, C++, Java)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">القدرة التحليلية</td><td class="px-4 py-2">منطق رياضي وتحليلي متقدم</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">ميزات المطورين</td><td class="px-4 py-2">استدعاء الوظائف (Function Calling) ونمط JSON</td></tr>
                            </tbody>
                        </table>
                    </div>
                `
            },
            "moonshotai/kimi-k2-thinking": {
                name: "Kimi 2 thinking",
                short_desc: "محرك تفكير استراتيجي يعتمد على Chain of Thought.",
                full_html: `
                    <h3 class="text-2xl font-bold mb-4">أتقن حل المشكلات المعقدة مع نموذج Kimi-K2</h3>
                    <p class="mb-4 text-gray-600 dark:text-gray-300">يُعد Kimi-K2 نموذجاً رائداً في مجال "التفكير المنطقي" من تطوير Moonshot AI، حيث تم تصميمه خصيصاً لمواجهة التحديات الفكرية الأكثر طلباً. على عكس النماذج التقليدية، يستخدم Kimi-K2 عملية "تفكير" داخلية متطورة تتيح له التحقق من الحقائق واستكشاف مسارات منطقية متعددة قبل تقديم الإجابة النهائية. هذا يجعله أداة لا غنى عنها في الأبحاث العلمية، البراهين الرياضية المتقدمة، وتصميمات الأنظمة البرمجية المعقدة.</p>
                    <p class="mb-6 text-gray-600 dark:text-gray-300">من خلال دمج Kimi-K2 عبر الـ API الخاص بنا، ستحصل على نموذج لا يكتفي بتوليد النصوص فحسب، بل "يفكر" بعمق في المهام المسندة إليه. ينافس هذا النموذج بقوة ويتفوق أحياناً على النماذج المغلقة الرائدة مثل o1-preview في اختبارات الرياضيات المعقدة ومنطق البرمجة. إذا كان مشروعك يتطلب درجة عالية من الدقة وتسلسلاً منطقياً صارماً، فإن Kimi-K2 هو الحل الأمثل لبناء وكلاء ذكاء اصطناعي من الفئة العليا.</p>
                    <h4 class="text-xl font-bold mb-4 text-primary">جدول المواصفات الفنية:</h4>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm text-right border border-gray-200 dark:border-gray-700">
                            <thead class="bg-gray-100 dark:bg-gray-800 uppercase font-bold">
                                <tr><th class="px-4 py-3">الميزة</th><th class="px-4 py-3">المواصفات</th></tr>
                            </thead>
                            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                                <tr><td class="px-4 py-2 font-semibold">اسم النموذج</td><td class="px-4 py-2">Kimi-K2 (نموذج التفكير المنطقي)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">تاريخ الإصدار</td><td class="px-4 py-2">6 نوفمبر 2025</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">نقطة القوة الأساسية</td><td class="px-4 py-2">التفكير المبني على التعلم المعزز (RL)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">نافذة السياق</td><td class="px-4 py-2">128,000 توكن</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">مستوى الأداء</td><td class="px-4 py-2">ينافس نموذج o1 في الرياضيات والعلوم (STEM)</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">البرمجة</td><td class="px-4 py-2">برمجة الأنظمة المتقدمة وتصحيح الأخطاء المنطقي</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">نمط الاستجابة</td><td class="px-4 py-2">سلسلة أفكار (CoT) في الوقت الفعلي</td></tr>
                                <tr><td class="px-4 py-2 font-semibold">دعم اللغات</td><td class="px-4 py-2">شامل (محسن للغات العربية، الإنجليزية، والصينية)</td></tr>
                            </tbody>
                        </table>
                    </div>
                `
            }
        }
    }
};