# SearchAD — nên hiểu paper này thế nào trong RAV-26

**Status: RESEARCH EVIDENCE + CONFIRMED INTERPRETATION**

## Paper

**SearchAD: Large-Scale Rare Image Retrieval Dataset for Autonomous Driving**  
CVPR 2026 — Felix Embacher, Jonas Uhrig, Marius Cordts, Markus Enzweiler.

Nguồn:  
https://openaccess.thecvf.com/content/CVPR2026/html/Embacher_SearchAD_Large-Scale_Rare_Image_Retrieval_Dataset_for_Autonomous_Driving_CVPR_2026_paper.html

## SearchAD thực sự đóng góp gì

Paper giới thiệu:
- hơn 423k frame;
- dữ liệu từ 11 established AD datasets;
- hơn 513k manual bounding boxes;
- 90 rare categories;
- benchmark semantic image retrieval;
- text-to-image và image-to-image retrieval;
- benchmark/fine-tuning cho retrieval models.

Nó nhắm tới bài toán tìm “needle in a haystack” trong dataset autonomous-driving rất lớn.

## Điều SearchAD không giải quyết thay RAV-26

SearchAD không phải:
- data-curation product hoàn chỉnh;
- temporal video-selection system;
- metric cho “data mới có giá trị hơn dataset cũ”;
- bằng chứng rằng 90 class nên trở thành ontology của RAV-26;
- một gallery production nên nhập thẳng vào hệ thống.

## Cách hiểu đã được chốt

RAV-26 **không cần phát triển retrieval model để compete SearchAD**.

Điểm đáng học từ SearchAD:
- rare/long-tail data trong AD có thể được formalize thành retrieval problem;
- natural-language/semantic query là một cách biểu diễn nhu cầu tìm dữ liệu;
- rare-case retrieval có benchmark rõ ràng;
- retrieval relevance và “marginal data value” là hai bài toán khác nhau.

Vì vậy SearchAD được giữ trong pack như **research context về long-tail / rare-case retrieval**, không phải success metric của MVP frame selection.
