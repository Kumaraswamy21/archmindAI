package example.orders;

import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {
    public void save(String orderId) {
        System.out.println(orderId);
    }

    public void delete(String orderId) {
        System.out.println(orderId);
    }
}
